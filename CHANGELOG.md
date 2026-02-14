# Changelog

All notable changes to CoreZero will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2025-02

### Added

- **Core RT Audio Engine**
  - Lock-free SPSC ring buffer (1M samples)
  - Real-time playback with zero underruns (proven @600sec)
  - Cross-platform support (Windows, Linux, macOS)

- **Audio Format Support**
  - FLAC (16/24/32-bit)
  - WAV (PCM, all bit depths)
  - MP3 (basic)
  - All sample formats: S16, S24, S32, F32, F64, U8, U16, U24, U32

- **Real-Time Metrics**
  - Underrun detection and counting
  - Callback latency measurement
  - Buffer depth tracking
  - CSV export for verification

- **CLI Interface**
  - Basic playback: `corezero "file.flac"`
  - Metrics display: `--metrics` flag
  - Stress testing: `--stress <seconds>`
  - CSV export: `--csv-export <file>`

- **Platform-Specific Features**
  - Windows: THREAD_PRIORITY_TIME_CRITICAL thread elevation
  - Linux/macOS: Portable implementation ready for SCHED_FIFO

- **Documentation**
  - Comprehensive README
  - Build and test guide
  - Architecture documentation
  - FAQ with 40+ questions

### Performance

- Zero underruns verified @600 seconds stress test
- <5% CPU usage @48kHz
- <50MB memory usage
- ~5MB executable size

### Known Limitations

- CLI interface only (no GUI)
- 48kHz fully tested, 96kHz compatible
- WASAPI Exclusive not supported (Phase 2)
- No DSP/EQ/upsampling (Phase 1.2)
- No multi-room streaming (Phase 2)
- File repeats on stress test only

---

## [1.1.0] - Planned (2 weeks)

### Planned Additions

- [ ] DSD support (DoP format)
- [ ] ALAC decoding
- [ ] AAC decoding
- [ ] File integrity verification
- [ ] Better error messages
- [ ] Linux ALSA optimization
- [ ] macOS Core Audio optimization

### Planned Fixes

- [ ] Clean up compiler warnings
- [ ] Improve error handling
- [ ] Better documentation for edge cases

---

## [1.2.0] - Planned (4 weeks)

### Planned Features

- [ ] Advanced audio formats (Opus, Vorbis, WMA, APE)
- [ ] Smooth upsampling (48k → 96k/192k)
- [ ] Preset EQ filters (Bright, Warm, Flat)
- [ ] Dithering for 16-bit output
- [ ] Volume normalization
- [ ] Gapless playback
- [ ] ReplayGain support
- [ ] Linux ALSA full support
- [ ] macOS optimization

---

## [2.0.0] - Planned (Pro, 8 weeks)

### CoreZero Pro Features

- [ ] WASAPI Exclusive (Windows, 192kHz support)
- [ ] Web dashboard (Axum + HTMX)
- [ ] Network streaming (SMB/NFS)
- [ ] Advanced upsampling filters
- [ ] REW integration (measurement export)
- [ ] Multi-room streaming (beta)
- [ ] Advanced audio formats

### Core Engine Improvements

- [ ] DSD full support
- [ ] ALSA exclusive mode (Linux)
- [ ] Core Audio exclusive (macOS)
- [ ] <5ms latency target
- [ ] Improved metrics

---

## [2.5.0] - Planned (Enterprise, 6 months)

### Features

- [ ] Multi-user account management
- [ ] Cloud library synchronization
- [ ] Advanced DSP library (paid DLC)
- [ ] Mastering-grade EQ
- [ ] Professional filter packs
- [ ] Cloud backup

---

## Breaking Changes

None yet. All releases maintain backward compatibility.

---

## Migration Guides

### v1.0 → v1.1

No breaking changes. Update with:
```bash
git pull origin main
cargo build --release
```

---

## Deprecations

### Planned for v2.0

- `cpal` backend (will add WASAPI Exclusive)
- CLI-only interface (will add Web UI)

---

## Security

See [SECURITY.md](SECURITY.md) for security-related changes.

---

## Contributors

### v1.0 Contributors

- Core development: [Your Name]
- Testing & feedback: Community

### Special Thanks

- Symphonia library (audio decoding)
- cpal library (audio output)
- Rust community

---

## How to Update

```bash
# Pull latest changes
git pull origin main

# Rebuild
cargo clean
cargo build --release

# Verify
./target/release/corezero --help
```

---

## Roadmap

See [README.md](README.md#roadmap) for detailed roadmap.

---

## Release Notes

### v1.0.0 (2025-02)

**Initial Release**

- Proven zero underruns @600sec
- Lock-free RT audio engine
- Cross-platform support
- MIT License

**Download:** [GitHub Releases](https://github.com/yourusername/corezero/releases)

---

**Full commit history:** [GitHub Commits](https://github.com/yourusername/corezero/commits/main)
