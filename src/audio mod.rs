pub mod engine;
pub mod driver;
pub mod metrics;
pub mod ring_buffer;
pub mod scheduler;

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use ring_buffer::RingBuffer;
use metrics::Metrics;

static GLOBAL_METRICS: std::sync::OnceLock<Arc<Metrics>> = std::sync::OnceLock::new();
static RUNNING: AtomicBool = AtomicBool::new(true);

pub fn run(path: String) -> std::thread::JoinHandle<()> {
    let buffer = Arc::new(RingBuffer::new());
    let metrics = Arc::new(Metrics::new());
    GLOBAL_METRICS.set(metrics.clone()).unwrap();

    // 재생 스레드
    let audio_thread = std::thread::spawn({
        let buffer = buffer.clone();
        let metrics = metrics.clone();
        move || {
            if scheduler::elevate_to_realtime(0x8).is_err() {
                eprintln!("⚠️  Failed to elevate thread priority (OK on non-Windows)");
            }
            driver::start_output(buffer, metrics);
        }
    });

    // 디코드 스레드
    let _decode_thread = std::thread::spawn({
        let buffer = buffer.clone();
        move || {
            engine::decode_to_buffer(path, &buffer);
        }
    });

    audio_thread
}

pub fn stop_audio() {
    RUNNING.store(false, Ordering::SeqCst);
}

pub fn is_running() -> bool {
    RUNNING.load(Ordering::SeqCst)
}

pub fn print_final_metrics(duration_sec: u64) {
    if let Some(metrics) = GLOBAL_METRICS.get() {
        metrics.print_final(duration_sec);
    }
}

pub fn export_metrics_csv(filename: &str) {
    if let Some(metrics) = GLOBAL_METRICS.get() {
        metrics.export_csv(filename);
    }
}