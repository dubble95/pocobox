// PocoBox Decoupled WebRTC AEC3 + Squeezelite VISEXPORT Shared Memory Reader
//
// Compiles against Alpine/postmarketOS webrtc-audio-processing-1 (or -2) into
// /usr/local/lib/libpocobox_aec3.so so Python can run full WebRTC AEC3 on
// microphone capture using Squeezelite's non-blocking POSIX shm reference tap
// (/dev/shm/squeezelite-<MAC>) WITHOUT touching bit-perfect ALSA USB DAC output.

#include <modules/audio_processing/include/audio_processing.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace {

// Must match ralph-irving/squeezelite output_vis.c layout on the same host ABI.
constexpr uint32_t kVisBufSize = 16384;

struct SqueezeliteVisShm {
    pthread_rwlock_t rwlock;
    uint32_t buf_size;
    uint32_t buf_index;
    bool running;
    uint32_t rate;
    time_t updated;
    int16_t buffer[kVisBufSize];
};

struct PocoboxAec3Handle {
    decltype(webrtc::AudioProcessingBuilder().Create()) apm;
    int sample_rate;
    int render_channels;
    int capture_channels;
    int frames_per_10ms;
    webrtc::StreamConfig render_cfg;
    webrtc::StreamConfig capture_cfg;
    std::vector<int16_t> render_scratch;

    PocoboxAec3Handle(int sr, int rch, int cch)
        : sample_rate(sr),
          render_channels(rch),
          capture_channels(cch),
          frames_per_10ms(sr / 100),
          render_cfg(sr, rch),
          capture_cfg(sr, cch),
          render_scratch(static_cast<size_t>(sr / 100) * static_cast<size_t>(rch), 0) {}
};

struct PocoboxVisHandle {
    int fd = -1;
    bool writable = false;
    SqueezeliteVisShm* shm = nullptr;
    uint32_t last_index = 0;
    bool initialized = false;
};

}  // namespace

extern "C" {

void* pocobox_aec3_create(int sample_rate, int render_channels, int capture_channels, int enable_hp_filter) {
    if (sample_rate <= 0 || (sample_rate % 100) != 0) {
        return nullptr;
    }
    if (render_channels < 1 || render_channels > 2 || capture_channels < 1 || capture_channels > 2) {
        return nullptr;
    }

    auto* h = new PocoboxAec3Handle(sample_rate, render_channels, capture_channels);
    h->apm = webrtc::AudioProcessingBuilder().Create();
    if (!h->apm) {
        delete h;
        return nullptr;
    }

    webrtc::AudioProcessing::Config cfg;
    cfg.echo_canceller.enabled = true;
    cfg.echo_canceller.mobile_mode = false;  // Full AEC3 (linear + shadow + residual echo suppressor)
    cfg.high_pass_filter.enabled = (enable_hp_filter != 0);
    cfg.noise_suppression.enabled = false;   // Keep quiet 2-3m speech intact
    cfg.gain_controller1.enabled = false;
    cfg.gain_controller2.enabled = false;
    h->apm->ApplyConfig(cfg);
    return h;
}

int pocobox_aec3_set_delay_ms(void* handle, int delay_ms) {
    if (!handle) {
        return -1;
    }
    auto* h = static_cast<PocoboxAec3Handle*>(handle);
    if (delay_ms < 0) delay_ms = 0;
    if (delay_ms > 500) delay_ms = 500;
    return h->apm->set_stream_delay_ms(delay_ms);
}

int pocobox_aec3_process_render(void* handle, const int16_t* render_interleaved) {
    if (!handle || !render_interleaved) {
        return -1;
    }
    auto* h = static_cast<PocoboxAec3Handle*>(handle);
    return h->apm->ProcessReverseStream(
        render_interleaved,
        h->render_cfg,
        h->render_cfg,
        h->render_scratch.data()
    );
}

int pocobox_aec3_process_capture(void* handle, const int16_t* capture_in, int16_t* capture_out) {
    if (!handle || !capture_in || !capture_out) {
        return -1;
    }
    auto* h = static_cast<PocoboxAec3Handle*>(handle);
    return h->apm->ProcessStream(
        capture_in,
        h->capture_cfg,
        h->capture_cfg,
        capture_out
    );
}

void pocobox_aec3_destroy(void* handle) {
    if (!handle) {
        return;
    }
    delete static_cast<PocoboxAec3Handle*>(handle);
}

void* pocobox_vis_open(const char* shm_name) {
    if (!shm_name || shm_name[0] == '\0') {
        return nullptr;
    }
    bool writable = true;
    int fd = shm_open(shm_name, O_RDWR, 0);
    if (fd < 0) {
        writable = false;
        fd = shm_open(shm_name, O_RDONLY, 0);
        if (fd < 0) {
            return nullptr;
        }
    }

    struct stat st {};
    if (fstat(fd, &st) != 0 || static_cast<size_t>(st.st_size) < sizeof(SqueezeliteVisShm)) {
        close(fd);
        return nullptr;
    }

    int prot = writable ? (PROT_READ | PROT_WRITE) : PROT_READ;
    void* mem = mmap(nullptr, sizeof(SqueezeliteVisShm), prot, MAP_SHARED, fd, 0);
    if (mem == MAP_FAILED) {
        close(fd);
        return nullptr;
    }

    auto* h = new PocoboxVisHandle();
    h->fd = fd;
    h->writable = writable;
    h->shm = static_cast<SqueezeliteVisShm*>(mem);
    h->last_index = 0;
    h->initialized = false;
    return h;
}

// Reads newly written interleaved stereo int16 samples from Squeezelite's ring buffer.
// Returns number of stereo frames written to out_stereo (>= 0), or -1 on error.
int pocobox_vis_read_new(
    void* handle,
    int16_t* out_stereo,
    int max_frames,
    uint32_t* out_rate,
    int* out_running
) {
    if (!handle || !out_stereo || max_frames <= 0) {
        return -1;
    }
    auto* h = static_cast<PocoboxVisHandle*>(handle);
    if (!h->shm) {
        return -1;
    }

    bool locked = false;
    if (h->writable) {
        if (pthread_rwlock_tryrdlock(&h->shm->rwlock) == 0) {
            locked = true;
        } else {
            // Squeezelite is currently writing; skip this tick without ever blocking audio playback.
            return 0;
        }
    }

    const uint32_t buf_size = (h->shm->buf_size > 0 && h->shm->buf_size <= kVisBufSize)
                                  ? h->shm->buf_size
                                  : kVisBufSize;
    const uint32_t cur_idx = h->shm->buf_index % buf_size;
    const bool running = h->shm->running;
    const uint32_t rate = h->shm->rate;
    const time_t updated = h->shm->updated;
    const time_t now = std::time(nullptr);
    const bool fresh = running && (rate > 0) && (now - updated <= 2);

    if (out_rate) *out_rate = rate;
    if (out_running) *out_running = fresh ? 1 : 0;

    if (!fresh) {
        h->last_index = cur_idx;
        h->initialized = true;
        if (locked) pthread_rwlock_unlock(&h->shm->rwlock);
        return 0;
    }

    if (!h->initialized) {
        h->last_index = cur_idx;
        h->initialized = true;
        if (locked) pthread_rwlock_unlock(&h->shm->rwlock);
        return 0;
    }

    // Distance in int16 samples (2 samples = 1 stereo frame)
    uint32_t avail_samples = (cur_idx >= h->last_index)
                                 ? (cur_idx - h->last_index)
                                 : (buf_size - h->last_index + cur_idx);
    uint32_t avail_frames = avail_samples / 2;
    uint32_t frames_to_copy = std::min<uint32_t>(avail_frames, static_cast<uint32_t>(max_frames));
    uint32_t start_sample = (avail_frames > frames_to_copy)
                                ? ((cur_idx + buf_size - frames_to_copy * 2) % buf_size)
                                : h->last_index;

    uint32_t samples_to_copy = frames_to_copy * 2;
    if (samples_to_copy > 0) {
        uint32_t first_part = std::min<uint32_t>(samples_to_copy, buf_size - start_sample);
        std::memcpy(out_stereo, &h->shm->buffer[start_sample], first_part * sizeof(int16_t));
        if (samples_to_copy > first_part) {
            std::memcpy(
                out_stereo + first_part,
                &h->shm->buffer[0],
                (samples_to_copy - first_part) * sizeof(int16_t)
            );
        }
    }

    h->last_index = cur_idx;
    if (locked) pthread_rwlock_unlock(&h->shm->rwlock);
    return static_cast<int>(frames_to_copy);
}

void pocobox_vis_close(void* handle) {
    if (!handle) {
        return;
    }
    auto* h = static_cast<PocoboxVisHandle*>(handle);
    if (h->shm) {
        munmap(h->shm, sizeof(SqueezeliteVisShm));
        h->shm = nullptr;
    }
    if (h->fd >= 0) {
        close(h->fd);
        h->fd = -1;
    }
    delete h;
}

}  // extern "C"
