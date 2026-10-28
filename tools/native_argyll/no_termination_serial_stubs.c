/* Original inert environment for pinned UNIX serial port setup functions.
 * Local termios declarations supply data only: every OS/device call is redirected
 * below, including open/close/configuration and sleep. No device is touched.
 * The termios data shape and symbols are substituted too; they do not assert
 * the host's serial ABI or flag values. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned int speed_t;
struct termios {
    unsigned int c_iflag, c_oflag, c_cflag, c_lflag;
    unsigned char c_cc[4];
};
enum { VTIME, VMIN, VSTART, VSTOP };
enum {
    IGNBRK = 1u << 0, CREAD = 1u << 1, CLOCAL = 1u << 2,
    IXON = 1u << 3, IXOFF = 1u << 4, CCTS_OFLOW = 1u << 5,
    CRTS_IFLOW = 1u << 6, CDSR_OFLOW = 1u << 7, CDTR_IFLOW = 1u << 8,
    INPCK = 1u << 9, PARENB = 1u << 10, PARODD = 1u << 11,
    CSTOPB = 1u << 12, CS5 = 0, CS6 = 1u << 13, CS7 = 2u << 13,
    CS8 = 3u << 13, TCIOFLUSH = 0, TCSANOW = 0,
};
enum {
    B110 = 110, B300 = 300, B600 = 600, B1200 = 1200, B2400 = 2400,
    B4800 = 4800, B9600 = 9600, B19200 = 19200, B38400 = 38400,
    B57600 = 57600, B115200 = 115200, B230400 = 230400,
};

#define UNIX_APPLE 1
#define ICOM_OK 0
#define ICOM_SYS 0x020000
#define icomt_serial 1
#define a1logd(...) ((void)0)
#define a1loge(...) ((void)0)
#define O_RDWR 1
#define O_NOCTTY 2
#ifndef B921600
#define B921600 921600
#endif

typedef enum { fc_nc, fc_None, fc_XonXOff, fc_Hardware, fc_HardwareDTR } flow_control;
typedef enum {
    baud_nc, baud_110, baud_300, baud_600, baud_1200, baud_2400, baud_4800,
    baud_9600, baud_19200, baud_38400, baud_57600, baud_115200, baud_230400, baud_921600
} baud_rate;
typedef enum { parity_nc, parity_none, parity_odd, parity_even } parity;
typedef enum { stop_nc, stop_1, stop_2 } stop_bits;
typedef enum { length_nc, length_5, length_6, length_7, length_8 } word_length;
typedef struct icoms icoms;
struct icoms {
    void *log;
    char *name, *spath;
    int is_open, fd, fc, br, py, sb, wl;
    int (*port_type)(icoms *);
    void (*close_port)(icoms *);
    void (*write)(void);
    void (*read)(void);
};
static int opens, closes, get_calls, set_calls, sleeps, failure, kind, termination_calls;
static int mock_open(const char *path, int flags) {
    (void)path; (void)flags; opens++; return failure == 1 ? -1 : 42;
}
static int mock_close(int fd) { (void)fd; closes++; return 0; }
static int mock_tcgetattr(int fd, struct termios *tio) {
    (void)fd; memset(tio, 0, sizeof(*tio)); get_calls++; return failure == 2 ? -1 : 0;
}
static int mock_tcsetattr(int fd, int when, const struct termios *tio) {
    (void)fd; (void)when; (void)tio; set_calls++; return failure == 3 ? -1 : 0;
}
static int mock_tcflush(int fd, int queue) { (void)fd; (void)queue; return 0; }
static int mock_speed(struct termios *tio, speed_t speed) { (void)tio; (void)speed; return 0; }
static void msec_sleep(int milliseconds) { (void)milliseconds; sleeps++; }
static int mock_port_type(icoms *p) { (void)p; return kind; }
static void mock_close_port(icoms *p) { p->is_open = 0; closes++; }
static void icoms_ser_write(void) { abort(); }
static void icoms_ser_read(void) { abort(); }

#define open mock_open
#define close mock_close
#define tcgetattr mock_tcgetattr
#define tcsetattr mock_tcsetattr
#define tcflush mock_tcflush
#define cfsetispeed mock_speed
#define cfsetospeed mock_speed
#define kill(...) (++termination_calls, -1)
#define TerminateProcess(...) (++termination_calls, 0)
#define kill_nprocess(...) (++termination_calls, -1)
#define kkill_nprocess(...) (++termination_calls, NULL)

/* CM_UPSTREAM_FUNCTIONS */

static int run_case(const char *name, int fault, int already_open, int port) {
    icoms communication = {0};
    int result, success;
    failure = fault; kind = port;
    opens = closes = get_calls = set_calls = sleeps = termination_calls = 0;
    communication.name = communication.spath = "synthetic-no-device";
    communication.is_open = already_open;
    communication.port_type = mock_port_type; communication.close_port = mock_close_port;
    result = icoms_set_ser_port(&communication, fc_None, baud_9600, parity_none, stop_1, length_8);
    success = port != icomt_serial || fault == 0;
    if ((result == ICOM_OK) != success || termination_calls
        || opens != (port == icomt_serial && !already_open)
        || closes != (port == icomt_serial && fault == 3)) {
        fprintf(stderr, "Serial source-flow case failed: %s\n", name); return 1;
    }
    printf("%s{\"case\":\"%s\",\"open_attempts\":%d,\"termination_calls\":%d,"
           "\"close_calls\":%d,\"success\":%s}",
           !strcmp(name, "first_success") ? "" : ",", name, opens, termination_calls,
           closes, success ? "true" : "false");
    return 0;
}

int main(void) {
    printf("{\"patched\":%s,\"cases\":[", CM_PATCHED ? "true" : "false");
    if (run_case("first_success", 0, 0, icomt_serial)
        || run_case("already_open", 0, 1, icomt_serial)
        || run_case("open_failure", 1, 0, icomt_serial)
        || run_case("get_attributes_failure", 2, 0, icomt_serial)
        || run_case("set_attributes_failure", 3, 0, icomt_serial)
        || run_case("wrong_transport", 0, 0, 2)) return 1;
    puts("]}"); return 0;
}
