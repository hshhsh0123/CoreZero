use std::sync::Arc;
use super::ring_buffer::RingBuffer;
use super::metrics::Metrics;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::time::Instant;
use std::cell::RefCell;

thread_local! {
    static LAST_CALLBACK: RefCell<Instant> = RefCell::new(Instant::now());
}

pub fn start_output(buffer: Arc<RingBuffer>, metrics: Arc<Metrics>) {
    let host = cpal::default_host();
    
    let device = match host.default_output_device() {
        Some(d) => d,
        None => {
            eprintln!("❌ No output device found");
            return;
        }
    };

    println!("🔊 Output device: {}", device.name().unwrap_or_default());

    let config = match device.default_output_config() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("❌ Failed to get output config: {}", e);
            return;
        }
    };

    println!("  Sample rate: {} Hz", config.sample_rate().0);
    println!("  Channels: {}", config.channels());

    let err_fn = |err| eprintln!("🔴 Stream error: {}", err);

    LAST_CALLBACK.with(|last| *last.borrow_mut() = Instant::now());

    let stream = device
        .build_output_stream(
            &config.into(),
            move |data: &mut [f32], _: &cpal::OutputCallbackInfo| {
                let now = Instant::now();
                LAST_CALLBACK.with(|last| {
                    let delta = now.duration_since(*last.borrow()).as_nanos() as u64;
                    metrics.update_max_gap(delta);
                    *last.borrow_mut() = now;
                });

                metrics.inc_callback();

                let depth = buffer.available_read() as u64;
                metrics.update_min_depth(depth);

                let mut idx = 0;
                while idx < data.len() {
                    if let Some(v) = buffer.pop() {
                        data[idx] = v;
                        idx += 1;
                    } else {
                        data[idx..].fill(0.0);
                        metrics.inc_underrun();
                        break;
                    }
                }
            },
            err_fn,
            None,
        )
        .expect("Failed to build stream");

    stream.play().expect("Failed to play");

    println!("▶️  Playing...");

    // 무한 대기 (프로그램이 종료되지 않도록)
    loop {
        std::thread::sleep(std::time::Duration::from_secs(1));
    }
}