use symphonia::core::{
    audio::{AudioBufferRef, Signal},
    codecs::DecoderOptions,
    formats::FormatOptions,
    io::MediaSourceStream,
    meta::MetadataOptions,
    probe::Hint,
};
use std::fs::File;
use std::path::Path;
use std::sync::Arc;
use super::ring_buffer::RingBuffer;

pub fn decode_to_buffer(path: String, buffer: &Arc<RingBuffer>) {
    let mut loop_count = 0;

    // RUNNING 플래그가 true인 동안 파일 반복 재생
    while super::is_running() {
        decode_file(&path, &buffer, loop_count);
        loop_count += 1;
    }

    println!("🛑 Decoder stopped");
}

fn decode_file(path: &str, buffer: &Arc<RingBuffer>, loop_num: u32) {
    let file = match File::open(Path::new(path)) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("❌ Failed to open file: {}", e);
            return;
        }
    };
    let mss = MediaSourceStream::new(Box::new(file), Default::default());

    let probed = match symphonia::default::get_probe()
        .format(&Hint::new(), mss, &FormatOptions::default(), &MetadataOptions::default())
    {
        Ok(p) => p,
        Err(e) => {
            eprintln!("❌ Failed to probe file: {}", e);
            return;
        }
    };

    let mut format = probed.format;
    let track = match format.default_track() {
        Some(t) => t,
        None => {
            eprintln!("❌ No audio track found");
            return;
        }
    };

    // 첫 루프에만 파일 정보 출력
    if loop_num == 0 {
        let sample_rate = track.codec_params.sample_rate.unwrap_or(48000);
        let channels = track.codec_params.channels.unwrap_or_default();
        println!("🎵 Audio file info:");
        println!("  Sample rate: {} Hz", sample_rate);
        println!("  Channels: {}", channels.count());
        if let Some(frames) = track.codec_params.n_frames {
            println!("  Duration: {:.2} sec", frames as f64 / sample_rate as f64);
        }
    }

    let sample_rate = track.codec_params.sample_rate.unwrap_or(48000);

    let mut decoder = match symphonia::default::get_codecs()
        .make(&track.codec_params, &DecoderOptions::default())
    {
        Ok(d) => d,
        Err(e) => {
            eprintln!("❌ Failed to create decoder: {}", e);
            return;
        }
    };

    let mut frame_count = 0;
    let mut format_logged = false;

    // 파일 디코딩 루프
    loop {
        // RUNNING 플래그 체크 (빠른 종료)
        if !super::is_running() {
            return;
        }

        let packet = match format.next_packet() {
            Ok(p) => p,
            Err(_) => {
                // 파일 끝 – 다음 루프를 위해 return
                if loop_num == 0 {
                    println!("✅ Decoded {} frames ({:.2} sec)", 
                             frame_count, 
                             frame_count as f64 / sample_rate as f64);
                }
                return;
            }
        };

        let decoded = match decoder.decode(&packet) {
            Ok(d) => d,
            Err(e) => {
                eprintln!("⚠️  Decode error: {}", e);
                continue;
            }
        };

        // 모든 오디오 포맷 처리
        match decoded {
            AudioBufferRef::F32(buf) => {
                if !format_logged && loop_num == 0 {
                    println!("  Format: F32");
                    format_logged = true;
                }
                process_buffer_f32(&buf, buffer, &mut frame_count);
            }
            AudioBufferRef::F64(buf) => {
                if !format_logged && loop_num == 0 {
                    println!("  Format: F64");
                    format_logged = true;
                }
                process_buffer_f64(&buf, buffer, &mut frame_count);
            }
            AudioBufferRef::S32(buf) => {
                if !format_logged && loop_num == 0 {
                    println!("  Format: S32");
                    format_logged = true;
                }
                process_buffer_s32(&buf, buffer, &mut frame_count);
            }
            AudioBufferRef::S24(buf) => {
                if !format_logged && loop_num == 0 {
                    println!("  Format: S24");
                    format_logged = true;
                }
                process_buffer_s24(&buf, buffer, &mut frame_count);
            }
            AudioBufferRef::S16(buf) => {
                if !format_logged && loop_num == 0 {
                    println!("  Format: S16");
                    format_logged = true;
                }
                process_buffer_s16(&buf, buffer, &mut frame_count);
            }
            AudioBufferRef::S8(buf) => {
                if !format_logged && loop_num == 0 {
                    println!("  Format: S8");
                    format_logged = true;
                }
                process_buffer_s8(&buf, buffer, &mut frame_count);
            }
            AudioBufferRef::U32(buf) => {
                if !format_logged && loop_num == 0 {
                    println!("  Format: U32");
                    format_logged = true;
                }
                process_buffer_u32(&buf, buffer, &mut frame_count);
            }
            AudioBufferRef::U24(buf) => {
                if !format_logged && loop_num == 0 {
                    println!("  Format: U24");
                    format_logged = true;
                }
                process_buffer_u24(&buf, buffer, &mut frame_count);
            }
            AudioBufferRef::U16(buf) => {
                if !format_logged && loop_num == 0 {
                    println!("  Format: U16");
                    format_logged = true;
                }
                process_buffer_u16(&buf, buffer, &mut frame_count);
            }
            AudioBufferRef::U8(buf) => {
                if !format_logged && loop_num == 0 {
                    println!("  Format: U8");
                    format_logged = true;
                }
                process_buffer_u8(&buf, buffer, &mut frame_count);
            }
        }
    }
}

fn process_buffer_f32(
    buf: &symphonia::core::audio::AudioBuffer<f32>,
    buffer: &Arc<RingBuffer>,
    frame_count: &mut u64,
) {
    for frame in 0..buf.frames() {
        let left = buf.chan(0)[frame];
        let right = if buf.spec().channels.count() > 1 {
            buf.chan(1)[frame]
        } else {
            left
        };
        while !buffer.push(left) {
            std::thread::yield_now();
        }
        while !buffer.push(right) {
            std::thread::yield_now();
        }
        *frame_count += 1;
    }
}

fn process_buffer_f64(
    buf: &symphonia::core::audio::AudioBuffer<f64>,
    buffer: &Arc<RingBuffer>,
    frame_count: &mut u64,
) {
    for frame in 0..buf.frames() {
        let left = buf.chan(0)[frame] as f32;
        let right = if buf.spec().channels.count() > 1 {
            buf.chan(1)[frame] as f32
        } else {
            left
        };
        while !buffer.push(left) {
            std::thread::yield_now();
        }
        while !buffer.push(right) {
            std::thread::yield_now();
        }
        *frame_count += 1;
    }
}

fn process_buffer_s32(
    buf: &symphonia::core::audio::AudioBuffer<i32>,
    buffer: &Arc<RingBuffer>,
    frame_count: &mut u64,
) {
    for frame in 0..buf.frames() {
        let left = (buf.chan(0)[frame] as f32) / 2147483648.0;
        let right = if buf.spec().channels.count() > 1 {
            (buf.chan(1)[frame] as f32) / 2147483648.0
        } else {
            left
        };
        while !buffer.push(left) {
            std::thread::yield_now();
        }
        while !buffer.push(right) {
            std::thread::yield_now();
        }
        *frame_count += 1;
    }
}

fn process_buffer_s24(
    buf: &symphonia::core::audio::AudioBuffer<symphonia::core::sample::i24>,
    buffer: &Arc<RingBuffer>,
    frame_count: &mut u64,
) {
    for frame in 0..buf.frames() {
        let left = (buf.chan(0)[frame].inner() as f32) / 8388608.0;
        let right = if buf.spec().channels.count() > 1 {
            (buf.chan(1)[frame].inner() as f32) / 8388608.0
        } else {
            left
        };
        while !buffer.push(left) {
            std::thread::yield_now();
        }
        while !buffer.push(right) {
            std::thread::yield_now();
        }
        *frame_count += 1;
    }
}

fn process_buffer_s16(
    buf: &symphonia::core::audio::AudioBuffer<i16>,
    buffer: &Arc<RingBuffer>,
    frame_count: &mut u64,
) {
    for frame in 0..buf.frames() {
        let left = (buf.chan(0)[frame] as f32) / 32768.0;
        let right = if buf.spec().channels.count() > 1 {
            (buf.chan(1)[frame] as f32) / 32768.0
        } else {
            left
        };
        while !buffer.push(left) {
            std::thread::yield_now();
        }
        while !buffer.push(right) {
            std::thread::yield_now();
        }
        *frame_count += 1;
    }
}

fn process_buffer_s8(
    buf: &symphonia::core::audio::AudioBuffer<i8>,
    buffer: &Arc<RingBuffer>,
    frame_count: &mut u64,
) {
    for frame in 0..buf.frames() {
        let left = (buf.chan(0)[frame] as f32) / 128.0;
        let right = if buf.spec().channels.count() > 1 {
            (buf.chan(1)[frame] as f32) / 128.0
        } else {
            left
        };
        while !buffer.push(left) {
            std::thread::yield_now();
        }
        while !buffer.push(right) {
            std::thread::yield_now();
        }
        *frame_count += 1;
    }
}

fn process_buffer_u32(
    buf: &symphonia::core::audio::AudioBuffer<u32>,
    buffer: &Arc<RingBuffer>,
    frame_count: &mut u64,
) {
    for frame in 0..buf.frames() {
        let left = ((buf.chan(0)[frame] as i64 - 2147483648) as f32) / 2147483648.0;
        let right = if buf.spec().channels.count() > 1 {
            ((buf.chan(1)[frame] as i64 - 2147483648) as f32) / 2147483648.0
        } else {
            left
        };
        while !buffer.push(left) {
            std::thread::yield_now();
        }
        while !buffer.push(right) {
            std::thread::yield_now();
        }
        *frame_count += 1;
    }
}

fn process_buffer_u24(
    buf: &symphonia::core::audio::AudioBuffer<symphonia::core::sample::u24>,
    buffer: &Arc<RingBuffer>,
    frame_count: &mut u64,
) {
    for frame in 0..buf.frames() {
        let left = ((buf.chan(0)[frame].inner() as i32 - 8388608) as f32) / 8388608.0;
        let right = if buf.spec().channels.count() > 1 {
            ((buf.chan(1)[frame].inner() as i32 - 8388608) as f32) / 8388608.0
        } else {
            left
        };
        while !buffer.push(left) {
            std::thread::yield_now();
        }
        while !buffer.push(right) {
            std::thread::yield_now();
        }
        *frame_count += 1;
    }
}

fn process_buffer_u16(
    buf: &symphonia::core::audio::AudioBuffer<u16>,
    buffer: &Arc<RingBuffer>,
    frame_count: &mut u64,
) {
    for frame in 0..buf.frames() {
        let left = ((buf.chan(0)[frame] as i32 - 32768) as f32) / 32768.0;
        let right = if buf.spec().channels.count() > 1 {
            ((buf.chan(1)[frame] as i32 - 32768) as f32) / 32768.0
        } else {
            left
        };
        while !buffer.push(left) {
            std::thread::yield_now();
        }
        while !buffer.push(right) {
            std::thread::yield_now();
        }
        *frame_count += 1;
    }
}

fn process_buffer_u8(
    buf: &symphonia::core::audio::AudioBuffer<u8>,
    buffer: &Arc<RingBuffer>,
    frame_count: &mut u64,
) {
    for frame in 0..buf.frames() {
        let left = ((buf.chan(0)[frame] as i16 - 128) as f32) / 128.0;
        let right = if buf.spec().channels.count() > 1 {
            ((buf.chan(1)[frame] as i16 - 128) as f32) / 128.0
        } else {
            left
        };
        while !buffer.push(left) {
            std::thread::yield_now();
        }
        while !buffer.push(right) {
            std::thread::yield_now();
        }
        *frame_count += 1;
    }
}