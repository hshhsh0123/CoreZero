mod audio;

use std::env;
use std::time::Duration;
use std::path::Path;

fn main() {
    let args: Vec<String> = env::args().collect();
    
    // 도움말
    if args.len() < 2 {
        print_usage(&args[0]);
        return;
    }

    let path = args[1].clone();
    
    // 파일 존재 확인
    if !Path::new(&path).exists() {
        eprintln!("❌ Error: File not found: {}", path);
        std::process::exit(1);
    }

    // 인자 파싱
    let mut stress_sec: Option<u64> = None;
    let mut show_metrics = false;
    let mut csv_export: Option<String> = None;

    let mut i = 2;
    while i < args.len() {
        match args[i].as_str() {
            "--stress" => {
                if i + 1 < args.len() {
                    if let Ok(sec) = args[i + 1].parse::<u64>() {
                        stress_sec = Some(sec);
                        i += 2;
                    } else {
                        eprintln!("❌ Error: Invalid stress seconds");
                        std::process::exit(1);
                    }
                } else {
                    eprintln!("❌ Error: --stress requires a value");
                    std::process::exit(1);
                }
            }
            "--metrics" => {
                show_metrics = true;
                i += 1;
            }
            "--csv-export" => {
                if i + 1 < args.len() {
                    csv_export = Some(args[i + 1].clone());
                    i += 2;
                } else {
                    eprintln!("❌ Error: --csv-export requires a filename");
                    std::process::exit(1);
                }
            }
            _ => {
                eprintln!("❌ Error: Unknown option: {}", args[i]);
                print_usage(&args[0]);
                std::process::exit(1);
            }
        }
    }

    println!("🎵 CoreZero v1.0 - Zero-Underrun Audio Engine");
    println!("Starting audio playback...");

    // 오디오 실행
    let audio_thread = audio::run(path.clone());

    // Stress 테스트 실행
    if let Some(sec) = stress_sec {
        println!("⏱️  Stress testing for {} seconds...", sec);
        std::thread::sleep(Duration::from_secs(sec));
        
        // 테스트 종료 신호
        audio::stop_audio();
        
        // 메트릭 출력
        audio::print_final_metrics(sec);
        
        // CSV 내보내기
        if let Some(filename) = csv_export {
            audio::export_metrics_csv(&filename);
            println!("✅ Metrics exported to: {}", filename);
        }
        
        std::process::exit(0);
    } else if show_metrics {
        // 메트릭만 표시하고 계속
        println!("📊 Real-time metrics enabled. Press Ctrl+C to stop.");
        println!("Note: Full metrics will show when audio finishes or you stop playback.");
        
        // 오디오 스레드 끝날 때까지 대기
        let _ = audio_thread.join();
    } else {
        // 일반 재생 (끝날 때까지)
        println!("▶️  Playing audio (Press Ctrl+C to stop)...");
        let _ = audio_thread.join();
    }
}

fn print_usage(program: &str) {
    eprintln!("\n📖 Usage: {} <audiofile> [options]\n", program);
    eprintln!("Arguments:");
    eprintln!("  <audiofile>              Path to audio file (FLAC, WAV, MP3)");
    eprintln!("\nOptions:");
    eprintln!("  --stress <seconds>       Run stress test for N seconds");
    eprintln!("  --metrics                Show real-time metrics");
    eprintln!("  --csv-export <file>      Export metrics to CSV");
    eprintln!("\nExamples:");
    eprintln!("  {} music.flac", program);
    eprintln!("  {} music.flac --metrics", program);
    eprintln!("  {} music.flac --stress 600", program);
    eprintln!("  {} music.flac --stress 600 --csv-export metrics.csv\n", program);
}