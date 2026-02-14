use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};
use std::fs::File;
use std::io::Write;

#[derive(Debug, Clone)]
pub struct Metrics {
    underruns: Arc<AtomicU64>,
    callback_count: Arc<AtomicU64>,
    max_callback_gap_ns: Arc<AtomicU64>,
    min_buffer_depth: Arc<AtomicU64>,
}

impl Metrics {
    pub fn new() -> Self {
        Self {
            underruns: Arc::new(AtomicU64::new(0)),
            callback_count: Arc::new(AtomicU64::new(0)),
            max_callback_gap_ns: Arc::new(AtomicU64::new(0)),
            min_buffer_depth: Arc::new(AtomicU64::new(u64::MAX)),
        }
    }

    pub fn inc_underrun(&self) {
        self.underruns.fetch_add(1, Ordering::Relaxed);
    }

    pub fn inc_callback(&self) {
        self.callback_count.fetch_add(1, Ordering::Relaxed);
    }

    pub fn update_max_gap(&self, gap_ns: u64) {
        let mut current = self.max_callback_gap_ns.load(Ordering::Relaxed);
        while gap_ns > current {
            match self.max_callback_gap_ns.compare_exchange_weak(
                current,
                gap_ns,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(x) => current = x,
            }
        }
    }

    pub fn update_min_depth(&self, depth: u64) {
        let mut current = self.min_buffer_depth.load(Ordering::Relaxed);
        while depth < current {
            match self.min_buffer_depth.compare_exchange_weak(
                current,
                depth,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(x) => current = x,
            }
        }
    }

    pub fn print_final(&self, duration_sec: u64) {
        let underruns = self.underruns.load(Ordering::Relaxed);
        let callbacks = self.callback_count.load(Ordering::Relaxed);
        let max_gap_ns = self.max_callback_gap_ns.load(Ordering::Relaxed);
        let min_depth = self.min_buffer_depth.load(Ordering::Relaxed);

        println!("\n");
        println!("═══════════════════════════════════════════════════");
        println!("     🎵 CoreZero RT Metrics Report ({} sec)", duration_sec);
        println!("═══════════════════════════════════════════════════");
        println!();
        println!("  Underruns:              {}", underruns);
        println!("  Callback count:         {}", callbacks);
        println!("  Max callback gap:       {} ns (~{:.2} ms)",
                 max_gap_ns,
                 max_gap_ns as f64 / 1_000_000.0);
        
        let min_depth_display = if min_depth == u64::MAX { 0 } else { min_depth };
        println!("  Min buffer depth:       {} samples", min_depth_display);
        
        println!();
        if underruns == 0 {
            println!("  ✅ ZERO UNDERRUNS - Perfect real-time performance!");
        } else {
            println!("  ⚠️  {} underruns detected", underruns);
        }
        println!();
        println!("═══════════════════════════════════════════════════");
        println!();
    }

    pub fn export_csv(&self, filename: &str) {
        let underruns = self.underruns.load(Ordering::Relaxed);
        let callbacks = self.callback_count.load(Ordering::Relaxed);
        let max_gap_ns = self.max_callback_gap_ns.load(Ordering::Relaxed);
        let min_depth = self.min_buffer_depth.load(Ordering::Relaxed);

        let csv_content = format!(
            "Metric,Value\n\
             Underruns,{}\n\
             Callback Count,{}\n\
             Max Callback Gap (ns),{}\n\
             Max Callback Gap (ms),{:.6}\n\
             Min Buffer Depth (samples),{}\n",
            underruns,
            callbacks,
            max_gap_ns,
            max_gap_ns as f64 / 1_000_000.0,
            if min_depth == u64::MAX { 0 } else { min_depth }
        );

        match File::create(filename) {
            Ok(mut file) => {
                if let Err(e) = file.write_all(csv_content.as_bytes()) {
                    eprintln!("❌ Failed to write CSV: {}", e);
                } else {
                    println!("✅ Metrics exported to: {}", filename);
                }
            }
            Err(e) => {
                eprintln!("❌ Failed to create CSV file: {}", e);
            }
        }
    }
}