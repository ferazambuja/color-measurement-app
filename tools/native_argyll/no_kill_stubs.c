/* Original inert environment for extracted, attributed Argyll function bodies.
 * No platform headers, OS libraries, device handles, signals or real services.
 * The generator inserts whole pinned functions at CM_UPSTREAM_FUNCTIONS.
 * This tests source control flow with substitute types, not OS ABI correctness. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define UNIX_APPLE 1
#define ENABLE_USB 1
#define ICOM_OK 0
#define ICOM_NOTS 0x001000
#define ICOM_SYS 0x020000
#define icomt_usb 2
#define icomuf_none 0
#define icomuf_no_open_clear 2
#define inst_ok 0
#define inst_coms_fail 1
#define kIOReturnSuccess 0
#define kIOUSBDeviceUserClientTypeID 1
#define kIOUSBInterfaceUserClientTypeID 2
#define kIOCFPlugInInterfaceID 3
#define DeviceInterfaceID 4
#define InterfaceInterfaceID 5
#define kCFAllocatorDefault 0
#define kNilOptions 0
#define kCFStringEncodingUTF8 0
#define kIOUSBFindInterfaceDontCare 0
#define kUSBSerialNumberString "synthetic"
#define IUSB_DESC_TYPE_STRING_MAX_SIZE 256
#define IUSB_ENDPOINT_NUM_MASK 0x0f
#define IUSB_ENDPOINT_DIR_MASK 0x80
#define IUSB_ENDPOINT_DIR_SHIFT 7
#define IUSB_ENDPOINT_TYPE_MASK 0x03
#define CFSTR(x) (x)
#define CFUUIDGetUUIDBytes(x) (x)
#define a1logd(...) ((void)0)
#define a1loge(...) ((void)0)
#define a1logw(...) ((void)0)
#define EPINFO(addr) ep[(((addr) >> 3) & 0x10) + ((addr) & 0x0f)]

typedef int IOReturn, SInt32, io_iterator_t, io_service_t;
typedef int icomuflags, inst_code, baud_rate, flow_control;
typedef uint8_t UInt8, u_int8_t;
typedef uint16_t UInt16;
typedef void *CFStringRef;
typedef struct {
    int bInterfaceClass, bInterfaceSubClass, bInterfaceProtocol, bAlternateSetting;
} IOUSBFindInterfaceRequest;
typedef struct plugin IOCFPlugInInterface;
typedef struct device device;
typedef struct interface interface;
struct plugin {
    int (*QueryInterface)(IOCFPlugInInterface **, int, void *);
    int (*Release)(IOCFPlugInInterface **);
    int (*Stop)(IOCFPlugInInterface **);
};
struct device {
    int (*USBDeviceOpenSeize)(device **);
    int (*Release)(device **);
    int (*SetConfiguration)(device **, int);
    int (*CreateInterfaceIterator)(device **, IOUSBFindInterfaceRequest *, io_iterator_t *);
};
struct interface {
    int (*USBInterfaceOpen)(interface **);
    int (*GetNumEndpoints)(interface **, u_int8_t *);
    int (*GetPipeProperties)(interface **, UInt8, UInt8 *, UInt8 *, UInt8 *, UInt16 *, UInt8 *);
};
typedef struct {
    int ioob, nifce;
    char *SerialNumber;
    device **device;
    interface **interfaces[32];
} usb_idevice;
typedef struct icoms icoms;
struct icoms {
    void *log;
    char *name;
    int debug, is_open, uflags, cconfig, wr_ep, rd_ep, rd_qa;
    usb_idevice *usbd;
    struct { int valid, addr, packetsize, type, ifaceno, pipe; } ep[32];
    int (*port_type)(icoms *);
    void (*close_port)(icoms *);
    int (*set_usb_port)(icoms *, int, int, int, int, int, char **);
    void (*usb_clearhalt)(icoms *, int);
    void (*write)(void);
    void (*read)(void);
};
typedef struct { void *log; icoms *icom; int gotcoms; } inst;
typedef inst i1pro3;
typedef struct { int result; } fake_thread;
typedef struct killer kkill_nproc_ctx;
struct killer { fake_thread *th; void (*del)(kkill_nproc_ctx *); };

static int failures, attempts, kill_calls, kill_deletes, closes, api_failure;
static int port_kind, sleeps, handlers, creates;
static void killer_delete(kkill_nproc_ctx *p) { (void)p; kill_deletes++; }
static fake_thread thread_state;
static kkill_nproc_ctx killer_state = {&thread_state, killer_delete};
static kkill_nproc_ctx *kkill_nprocess(char **names, void *log) {
    (void)log;
    if (!names || !names[0] || strcmp(names[0], "i1ProDeviceService")) abort();
    kill_calls++; return &killer_state;
}
static int mock_open(device **p) { (void)p; attempts++; return attempts <= failures ? -1 : 0; }
static int mock_release(device **p) { (void)p; return 0; }
static int mock_config(device **p, int config) { (void)p; return config == 1 ? 0 : -1; }
static int mock_iterator(device **p, IOUSBFindInterfaceRequest *r, io_iterator_t *it) {
    (void)p; (void)r; *it = 0; return 0;
}
static device device_state = {mock_open, mock_release, mock_config, mock_iterator};
static device *device_pointer = &device_state;
static int mock_query(IOCFPlugInInterface **p, int uuid, void *out) {
    (void)p;
    if (uuid != DeviceInterfaceID) abort();
    *(device ***)out = &device_pointer; return 0;
}
static int mock_plugin_release(IOCFPlugInInterface **p) { (void)p; return 0; }
static IOCFPlugInInterface plugin_state = {mock_query, mock_plugin_release, mock_plugin_release};
static IOCFPlugInInterface *plugin_pointer = &plugin_state;
static int IOCreatePlugInInterfaceForService(int object, int type, int iid,
                                           IOCFPlugInInterface ***out, SInt32 *score) {
    (void)object; (void)type; (void)iid; (void)score; creates++;
    *out = &plugin_pointer; return api_failure ? -1 : 0;
}
static CFStringRef IORegistryEntryCreateCFProperty(int o, const char *s, int a, int b) {
    (void)o; (void)s; (void)a; (void)b; return NULL;
}
static int CFStringGetCString(CFStringRef s, char *b, int n, int e) {
    (void)s; (void)b; (void)n; (void)e; abort();
}
static void CFRelease(CFStringRef s) { (void)s; abort(); }
static int IOIteratorNext(int iterator) { (void)iterator; return 0; }
static void IOObjectRelease(int object) { (void)object; }
static void IODestroyPlugInInterface(IOCFPlugInInterface **p) { (void)p; abort(); }
static void cleanup_device(icoms *p) { p->is_open = 0; }
static void usb_install_signal_handlers(icoms *p) { (void)p; handlers++; }
static void msec_sleep(int milliseconds) { (void)milliseconds; sleeps++; }
static int mock_port_type(icoms *p) { (void)p; return port_kind; }
static void mock_close(icoms *p) { closes++; p->is_open = 0; }
static void mock_clearhalt(icoms *p, int ep) { (void)p; (void)ep; abort(); }
static void icoms_usb_ser_write(void) { abort(); }
static void icoms_usb_ser_read(void) { abort(); }
static int icoms2i1pro3_err(int error) { return error; }
static int i1pro3_interp_code(i1pro3 *p, int error) { (void)p; return error; }

/* CM_UPSTREAM_FUNCTIONS */

static int run_case(const char *name, int fail_count, int already_open, int bad_api, int wrong_port) {
    usb_idevice usb = {0};
    icoms communication = {0};
    i1pro3 instrument = {0};
    int result, expected_attempts, expected_kills, success;
    failures = fail_count; api_failure = bad_api; port_kind = wrong_port ? 1 : icomt_usb;
    attempts = kill_calls = kill_deletes = closes = sleeps = handlers = creates = 0;
    communication.name = "synthetic-no-device";
    communication.usbd = &usb; communication.is_open = already_open;
    communication.port_type = mock_port_type; communication.close_port = mock_close;
    communication.set_usb_port = icoms_set_usb_port; communication.usb_clearhalt = mock_clearhalt;
    instrument.icom = &communication;
    result = i1pro3_init_coms(&instrument, 0, 0, 1.0);
    expected_attempts = (bad_api || wrong_port) ? 0 : fail_count >= 21 ? 21 : fail_count + 1;
    expected_kills = (!CM_PATCHED && fail_count > 0 && !bad_api && !wrong_port) ? 1 : 0;
    success = !bad_api && !wrong_port && fail_count < 21;
    if (attempts != expected_attempts || kill_calls != expected_kills
        || kill_deletes != expected_kills || instrument.gotcoms != success
        || (result == inst_ok) != success || handlers != success
        || closes != (already_open && !wrong_port)) return 1;
    printf("%s{\"case\":\"%s\",\"open_attempts\":%d,\"termination_calls\":%d,"
           "\"close_calls\":%d,\"success\":%s}",
           !strcmp(name, "first_success") ? "" : ",", name, attempts, kill_calls,
           closes, success ? "true" : "false");
    return 0;
}

int main(void) {
    printf("{\"patched\":%s,\"cases\":[", CM_PATCHED ? "true" : "false");
    if (run_case("first_success", 0, 0, 0, 0)
        || run_case("retry_success", 2, 0, 0, 0)
        || run_case("retry_exhausted", 99, 0, 0, 0)
        || run_case("reopen_retry", 1, 1, 0, 0)
        || run_case("wrong_transport", 0, 0, 0, 1)
        || run_case("api_failure", 0, 0, 1, 0)) return 1;
    puts("]}"); return 0;
}
