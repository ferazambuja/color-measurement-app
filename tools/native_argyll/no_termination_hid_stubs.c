/* Original inert environment for whole, attributed pinned Argyll functions.
 * Windows source branches use substitute types and hooks, not Windows headers
 * or libraries. No process lookup, OS handle, device, or thread is created. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define NT 1
#define ENABLE_USB 1
#define ICOM_OK 0
#define ICOM_SYS 0x020000
#define icomt_hid 3
#define icomt_usb 2
#define icomuf_none 0
#define icomuf_no_open_clear 2
#define inst_ok 0
#define inst_coms_fail 1
#define IUSB_DESC_TYPE_STRING_MAX_SIZE 256
#define GENERIC_READ 1
#define GENERIC_WRITE 2
#define OPEN_EXISTING 3
#define FILE_FLAG_OVERLAPPED 4
#define INVALID_HANDLE_VALUE ((void *)(intptr_t)-1)
#define a1logd(...) ((void)0)
#define a1loge(...) ((void)0)

typedef int icomuflags, inst_code, baud_rate, flow_control;
typedef uint16_t icmUTF16;
typedef struct { void *hEvent; } OVERLAPPED;
typedef struct {
    char *dpath, *SerialNumber;
    void *fh;
    OVERLAPPED ols;
} hid_idevice;
typedef struct icoms icoms;
struct icoms {
    void *log;
    char *name;
    int is_open, uflags;
    hid_idevice *hidd;
    int (*port_type)(icoms *);
    void (*close_port)(icoms *);
    int (*set_hid_port)(icoms *, int, int, char **);
    int (*set_usb_port)(icoms *, int, int, int, int, int, char **);
    void (*write)(void);
    void (*read)(void);
};
typedef struct { void *log; icoms *icom; int gotcoms; } inst;
typedef inst i1d3;

static int failures, attempts, kill_calls, closes, handlers, port_kind;
static int event_failure, status_failure, status_calls, usb_calls;
static int file_token, event_token;
static void *CreateFile(const char *path, int access, int share, void *security,
                        int disposition, int flags, void *template) {
    (void)path; (void)access; (void)share; (void)security;
    (void)disposition; (void)flags; (void)template;
    attempts++;
    return attempts <= failures ? INVALID_HANDLE_VALUE : &file_token;
}
static void *CreateEvent(void *security, int manual, int initial, void *name) {
    (void)security; (void)manual; (void)initial; (void)name;
    return event_failure ? NULL : &event_token;
}
static int GetLastError(void) { return 5; }
static int pHidD_GetSerialNumberString(void *handle, void *buffer, int size) {
    (void)handle; (void)buffer; (void)size; return 0;
}
static size_t icmUTF16toUTF8(void *unused, char *output, icmUTF16 *input) {
    (void)unused; (void)output; (void)input; abort();
}
static int kill_nprocess(char **names, void *log) {
    (void)log;
    if (!names || !names[0] || strcmp(names[0], "i1ProfilerTray.exe")) abort();
    kill_calls++; return 1;
}
static void msec_sleep(int milliseconds) { (void)milliseconds; }
static void hid_close_port(icoms *p) { closes++; p->is_open = 0; }
static void usb_install_signal_handlers(icoms *p) { (void)p; handlers++; }
static int mock_port_type(icoms *p) { (void)p; return port_kind; }
static int mock_usb(icoms *p, int config, int wr, int rd, int flags, int retry, char **names) {
    (void)config; (void)wr; (void)rd; (void)flags;
    if (retry || names) abort();
    usb_calls++; p->is_open = 1; return ICOM_OK;
}
static int i1d3_check_status(i1d3 *p, int *status) {
    (void)p; *status = 0; status_calls++; return status_failure ? 7 : inst_ok;
}
static int icoms2i1d3_err(int error, int unused) { (void)unused; return error; }
static int i1d3_interp_code(inst *p, int error) { (void)p; return error; }

/* CM_UPSTREAM_FUNCTIONS */

static int run_case(const char *name, int fail_count, int reopen, int event_error,
                    int status_error, int kind) {
    hid_idevice hid = {0};
    icoms communication = {0};
    i1d3 instrument = {0};
    int result, expected_attempts, expected_kills, open_success, success;
    failures = fail_count; event_failure = event_error; status_failure = status_error;
    port_kind = kind;
    attempts = kill_calls = closes = handlers = status_calls = usb_calls = 0;
    hid.dpath = "synthetic-no-device";
    communication.name = "synthetic-no-device";
    communication.hidd = &hid; communication.is_open = reopen;
    communication.port_type = mock_port_type; communication.close_port = hid_close_port;
    communication.set_hid_port = icoms_set_hid_port; communication.set_usb_port = mock_usb;
    instrument.icom = &communication;
    result = i1d3_init_coms(&instrument, 0, 0, 1.0);
    expected_attempts = kind != icomt_hid ? 0 : fail_count >= 3 ? 3 : fail_count + 1;
    expected_kills = !CM_PATCHED && kind == icomt_hid
                   ? (fail_count >= 3 ? 2 : fail_count >= 2 ? 1 : 0) : 0;
    open_success = kind == icomt_usb || (kind == icomt_hid && fail_count < 3 && !event_error);
    success = open_success && !status_error;
    if (attempts != expected_attempts || kill_calls != expected_kills
        || instrument.gotcoms != success || (result == inst_ok) != success
        || status_calls != open_success || usb_calls != (kind == icomt_usb)
        || handlers != (open_success && kind == icomt_hid)
        || closes != (reopen && kind == icomt_hid)) {
        fprintf(stderr, "HID source-flow case failed: %s\n", name); return 1;
    }
    printf("%s{\"case\":\"%s\",\"open_attempts\":%d,\"termination_calls\":%d,"
           "\"close_calls\":%d,\"status_calls\":%d,\"success\":%s}",
           !strcmp(name, "first_success") ? "" : ",", name, attempts, kill_calls,
           closes, status_calls, success ? "true" : "false");
    return 0;
}

int main(void) {
    printf("{\"patched\":%s,\"cases\":[", CM_PATCHED ? "true" : "false");
    if (run_case("first_success", 0, 0, 0, 0, icomt_hid)
        || run_case("retry_success", 2, 0, 0, 0, icomt_hid)
        || run_case("retry_exhausted", 99, 0, 0, 0, icomt_hid)
        || run_case("reopen_retry", 2, 1, 0, 0, icomt_hid)
        || run_case("wrong_transport", 0, 0, 0, 0, 1)
        || run_case("event_failure", 0, 0, 1, 0, icomt_hid)
        || run_case("status_failure", 0, 0, 0, 1, icomt_hid)
        || run_case("usb_fallback", 0, 0, 0, 0, icomt_usb)) return 1;
    puts("]}"); return 0;
}
