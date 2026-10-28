/* Original process-pipe adapter; no instrument or transport calls.
 * One reader owns stdin. Windows workers require an anonymous/named input pipe,
 * as supplied by Python subprocess; console input is deliberately unsupported.
 * PeekNamedPipe documents anonymous pipes and non-consuming availability checks:
 * https://learn.microsoft.com/windows/win32/api/namedpipeapi/nf-namedpipeapi-peeknamedpipe
 */
#ifndef CM_PLATFORM_IO_H
#define CM_PLATFORM_IO_H

#include <stdio.h>

enum { CM_IO_ERROR = -2, CM_IO_EOF = -1, CM_IO_PENDING = 0, CM_IO_BYTE = 1 };

#ifdef _WIN32
#include <windows.h>
#include <io.h>
#include <fcntl.h>
#include <stdlib.h>
#include <wchar.h>

/* The Windows CRT's narrow argv uses a code page, not our UTF-8 protocol.
 * Let its wide startup parse quoting, then convert each complete argument.
 * Invalid UTF-16 is refused before the application can select an instrument.
 * https://learn.microsoft.com/en-us/cpp/c-language/using-wmain
 * https://learn.microsoft.com/en-us/windows/win32/api/stringapiset/nf-stringapiset-widechartomultibyte
 */
static int cm_windows_main(int argc, wchar_t **wide, int (*run)(int, char **)) {
    char **argv, **owned;
    int result = 2, valid = 1;
    if (argc < 0) return 2;
    argv = calloc((size_t)argc + 1, 2 * sizeof(*argv));
    if (!argv) return 2;
    /* The description backend rewrites/shifts argv. Keep its mutable view
     * separate from allocation ownership, including on a partial failure. */
    owned = argv + (size_t)argc + 1;
    for (int i = 0; i < argc; i++) {
        int bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide[i], -1,
                                        NULL, 0, NULL, NULL);
        if (!bytes || !(argv[i] = owned[i] = malloc((size_t)bytes))
            || WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide[i], -1,
                                   argv[i], bytes, NULL, NULL) != bytes) {
            valid = 0;
            break;
        }
    }
    if (valid) result = run(argc, argv);
    else fputs("Unable to encode process arguments as UTF-8\n", stderr);
    for (int i = 0; i < argc; i++) free(owned[i]);
    free(argv);
    return result;
}

#define CM_PROCESS_MAIN(name) \
    static int name(int argc, char **argv); \
    int wmain(int argc, wchar_t **wide) { return cm_windows_main(argc, wide, name); } \
    static int name(int argc, char **argv)

/* Application file operands are UTF-8 too; narrow fopen would decode them
 * through a Windows code page. Binary mode retains the complete numeric input.
 * https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/fopen-wfopen
 */
static FILE *cm_open_input_file(const char *path) {
    int units = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
    wchar_t *wide;
    FILE *file = NULL;
    if (!units || !(wide = malloc((size_t)units * sizeof(*wide)))) return NULL;
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, wide, units) == units)
        file = _wfopen(wide, L"rb");
    free(wide);
    return file;
}

static int cm_pipe_failure(void) {
    DWORD error = GetLastError();
    return error == ERROR_BROKEN_PIPE || error == ERROR_HANDLE_EOF
        ? CM_IO_EOF : CM_IO_ERROR;
}

static int cm_stdin_byte(char *byte, int wait) {
    HANDLE input = (HANDLE)_get_osfhandle(_fileno(stdin));
    DWORD available, count;
    if (input == INVALID_HANDLE_VALUE || GetFileType(input) != FILE_TYPE_PIPE)
        return CM_IO_ERROR;
    if (!wait) {
        if (!PeekNamedPipe(input, NULL, 0, NULL, &available, NULL)) return cm_pipe_failure();
        if (!available) return CM_IO_PENDING;
    }
    if (!ReadFile(input, byte, 1, &count, NULL)) return cm_pipe_failure();
    return count == 1 ? CM_IO_BYTE : CM_IO_EOF;
}

static FILE *cm_open_wire(void) {
    int descriptor = _dup(_fileno(stdout));
    FILE *wire;
    if (descriptor < 0) return NULL;
    if (_setmode(descriptor, _O_BINARY) < 0
        || _setmode(_fileno(stdin), _O_BINARY) < 0) {
        _close(descriptor); return NULL;
    }
    wire = _fdopen(descriptor, "wb");
    if (!wire) { _close(descriptor); return NULL; }
    /* The Windows CRT treats _IOLBF as full buffering. Deliver requests before
     * waiting for a reply, including small allowance and operator frames. */
    if (setvbuf(wire, NULL, _IONBF, 0) != 0) { fclose(wire); return NULL; }
    if (_dup2(_fileno(stderr), _fileno(stdout)) < 0) { fclose(wire); return NULL; }
    return wire;
}

static void cm_sleep_ms(unsigned long milliseconds) { Sleep(milliseconds); }
/* Seconds on a clock that never steps; only differences are meaningful. */
static int cm_monotonic_checked(double *out) {
    *out = (double)GetTickCount64() / 1000.0;
    return 1;
}
static double cm_monotonic_s(void) {
    double value;
    return cm_monotonic_checked(&value) ? value : 0.0;
}

#else
#include <unistd.h>
#include <sys/select.h>
#include <errno.h>
#include <time.h>

#define CM_PROCESS_MAIN(name) int main(int argc, char **argv)

static FILE *cm_open_input_file(const char *path) { return fopen(path, "r"); }

static int cm_stdin_byte(char *byte, int wait) {
    for (;;) {
        fd_set input;
        struct timeval timeout = {0, 0};
        int ready;
        ssize_t count;
        FD_ZERO(&input); FD_SET(STDIN_FILENO, &input);
        ready = select(STDIN_FILENO + 1, &input, NULL, NULL, wait ? NULL : &timeout);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) return CM_IO_ERROR;
        if (!ready) return CM_IO_PENDING;
        count = read(STDIN_FILENO, byte, 1);
        if (count < 0 && errno == EINTR) continue;
        return count == 1 ? CM_IO_BYTE : count == 0 ? CM_IO_EOF : CM_IO_ERROR;
    }
}

static FILE *cm_open_wire(void) {
    int descriptor = dup(STDOUT_FILENO);
    FILE *wire;
    if (descriptor < 0) return NULL;
    wire = fdopen(descriptor, "w");
    if (!wire) { close(descriptor); return NULL; }
    if (setvbuf(wire, NULL, _IOLBF, 0) != 0) { fclose(wire); return NULL; }
    if (dup2(STDERR_FILENO, STDOUT_FILENO) < 0) { fclose(wire); return NULL; }
    return wire;
}

/* Seconds on a clock that never steps; only differences are meaningful. */
static int cm_monotonic_checked(double *out) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
    *out = (double)now.tv_sec + (double)now.tv_nsec / 1e9;
    return 1;
}
static double cm_monotonic_s(void) {
    double value;
    return cm_monotonic_checked(&value) ? value : 0.0;
}

static void cm_sleep_ms(unsigned long milliseconds) {
    struct timespec delay = {(time_t)(milliseconds / 1000),
                            (long)(milliseconds % 1000) * 1000000L};
    while (nanosleep(&delay, &delay) < 0 && errno == EINTR) {}
}
#endif
#endif
