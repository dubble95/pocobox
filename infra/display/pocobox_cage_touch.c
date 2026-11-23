/*
 * pocobox_cage_touch.c - PocoBox Touchscreen to Output Orientation Mapper for Cage
 *
 * In Cage kiosk compositor, touch input devices (such as Poco F1's ft5x06)
 * are initialized without a fixed output_name, and Cage fails to map them to
 * the internal display output (DSI-1). Because no mapped output is associated
 * with the cursor or touch input device in wlroots, wlroots skips applying
 * output rotation (apply_output_transform), causing touch coordinates to remain
 * unrotated (portrait) even when the display is rotated to landscape (270 / 90).
 *
 * This LD_PRELOAD library intercepts Cage's cursor and output layout calls:
 * 1. Automatically maps any attached touch/pointer input devices to the active output.
 * 2. Sets cursor->mapped_output so all subsequent motion/touch events automatically
 *    inherit the current wlr_output transform (handling dynamic auto-rotation).
 * 3. Logs layout touch coordinates for diagnostic verification.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <dlfcn.h>
#include <stdbool.h>

struct wlr_cursor;
struct wlr_input_device;
struct wlr_output;
struct wlr_output_layout;
struct wlr_output_layout_output;

#define MAX_DEVS 16

static struct wlr_cursor *g_cursor = NULL;
static struct wlr_output *g_output = NULL;
static struct wlr_input_device *g_devices[MAX_DEVS];
static int g_dev_count = 0;

static void (*real_wlr_cursor_attach_input_device)(struct wlr_cursor *cur, struct wlr_input_device *dev) = NULL;
static void (*real_wlr_cursor_detach_input_device)(struct wlr_cursor *cur, struct wlr_input_device *dev) = NULL;
static void (*real_wlr_cursor_attach_output_layout)(struct wlr_cursor *cur, struct wlr_output_layout *layout) = NULL;
static void (*real_wlr_cursor_map_to_output)(struct wlr_cursor *cur, struct wlr_output *output) = NULL;
static void (*real_wlr_cursor_map_input_to_output)(struct wlr_cursor *cur, struct wlr_input_device *dev, struct wlr_output *output) = NULL;
static void (*real_wlr_cursor_absolute_to_layout_coords)(struct wlr_cursor *cur, struct wlr_input_device *dev, double x, double y, double *lx, double *ly) = NULL;
static struct wlr_output_layout_output *(*real_wlr_output_layout_add_auto)(struct wlr_output_layout *layout, struct wlr_output *output) = NULL;
static struct wlr_output_layout_output *(*real_wlr_output_layout_add)(struct wlr_output_layout *layout, struct wlr_output *output, int x, int y) = NULL;

static void init_symbols(void) {
    if (!real_wlr_cursor_map_to_output) {
        real_wlr_cursor_map_to_output = dlsym(RTLD_NEXT, "wlr_cursor_map_to_output");
    }
    if (!real_wlr_cursor_map_input_to_output) {
        real_wlr_cursor_map_input_to_output = dlsym(RTLD_NEXT, "wlr_cursor_map_input_to_output");
    }
}

static void map_all_to_output(void) {
    init_symbols();
    if (!g_cursor || !g_output) return;
    if (real_wlr_cursor_map_to_output) {
        real_wlr_cursor_map_to_output(g_cursor, g_output);
        fprintf(stderr, "[pocobox-cage-touch] Set cursor->mapped_output = %p\n", (void *)g_output);
    }
    for (int i = 0; i < g_dev_count; i++) {
        if (real_wlr_cursor_map_input_to_output && g_devices[i]) {
            real_wlr_cursor_map_input_to_output(g_cursor, g_devices[i], g_output);
            fprintf(stderr, "[pocobox-cage-touch] Mapped input dev %p to output %p\n", (void *)g_devices[i], (void *)g_output);
        }
    }
}

void wlr_cursor_attach_input_device(struct wlr_cursor *cur, struct wlr_input_device *dev) {
    if (!real_wlr_cursor_attach_input_device) {
        real_wlr_cursor_attach_input_device = dlsym(RTLD_NEXT, "wlr_cursor_attach_input_device");
    }
    g_cursor = cur;
    real_wlr_cursor_attach_input_device(cur, dev);

    bool exists = false;
    for (int i = 0; i < g_dev_count; i++) {
        if (g_devices[i] == dev) { exists = true; break; }
    }
    if (!exists && g_dev_count < MAX_DEVS) {
        g_devices[g_dev_count++] = dev;
        fprintf(stderr, "[pocobox-cage-touch] Tracked input device %p (total %d)\n", (void *)dev, g_dev_count);
    }

    if (g_output) {
        map_all_to_output();
    }
}

void wlr_cursor_detach_input_device(struct wlr_cursor *cur, struct wlr_input_device *dev) {
    if (!real_wlr_cursor_detach_input_device) {
        real_wlr_cursor_detach_input_device = dlsym(RTLD_NEXT, "wlr_cursor_detach_input_device");
    }
    for (int i = 0; i < g_dev_count; i++) {
        if (g_devices[i] == dev) {
            g_devices[i] = g_devices[--g_dev_count];
            break;
        }
    }
    real_wlr_cursor_detach_input_device(cur, dev);
}

void wlr_cursor_attach_output_layout(struct wlr_cursor *cur, struct wlr_output_layout *layout) {
    if (!real_wlr_cursor_attach_output_layout) {
        real_wlr_cursor_attach_output_layout = dlsym(RTLD_NEXT, "wlr_cursor_attach_output_layout");
    }
    g_cursor = cur;
    real_wlr_cursor_attach_output_layout(cur, layout);
    if (g_output) {
        map_all_to_output();
    }
}

struct wlr_output_layout_output *wlr_output_layout_add_auto(struct wlr_output_layout *layout, struct wlr_output *output) {
    if (!real_wlr_output_layout_add_auto) {
        real_wlr_output_layout_add_auto = dlsym(RTLD_NEXT, "wlr_output_layout_add_auto");
    }
    struct wlr_output_layout_output *res = real_wlr_output_layout_add_auto(layout, output);
    g_output = output;
    fprintf(stderr, "[pocobox-cage-touch] Added output %p to layout\n", (void *)output);
    map_all_to_output();
    return res;
}

struct wlr_output_layout_output *wlr_output_layout_add(struct wlr_output_layout *layout, struct wlr_output *output, int x, int y) {
    if (!real_wlr_output_layout_add) {
        real_wlr_output_layout_add = dlsym(RTLD_NEXT, "wlr_output_layout_add");
    }
    struct wlr_output_layout_output *res = real_wlr_output_layout_add(layout, output, x, y);
    g_output = output;
    fprintf(stderr, "[pocobox-cage-touch] Added output %p to layout (at %d,%d)\n", (void *)output, x, y);
    map_all_to_output();
    return res;
}

void wlr_cursor_absolute_to_layout_coords(struct wlr_cursor *cur, struct wlr_input_device *dev, double x, double y, double *lx, double *ly) {
    if (!real_wlr_cursor_absolute_to_layout_coords) {
        real_wlr_cursor_absolute_to_layout_coords = dlsym(RTLD_NEXT, "wlr_cursor_absolute_to_layout_coords");
    }
    real_wlr_cursor_absolute_to_layout_coords(cur, dev, x, y, lx, ly);
    if (lx && ly) {
        fprintf(stderr, "[pocobox-cage-touch] Touch map: norm=(%.3f, %.3f) -> layout=(%.1f, %.1f)\n", x, y, *lx, *ly);
    }
}
