# CoreZero v1.0: 빌드 및 테스트 가이드

## 📋 목차

1. [환경 준비](#환경-준비)
2. [파일 구조 설정](#파일-구조-설정)
3. [코드 복사](#코드-복사)
4. [빌드](#빌드)
5. [테스트](#테스트)
6. [문제 해결](#문제-해결)

---

## ✅ 환경 준비

### 요구사항

- **Rust 1.70+** (2023년 6월 이후)
- **Cargo** (Rust 설치 시 자동 포함)
- **Windows 10/11** (또는 macOS, Linux)

### 설치 확인

```bash
# PowerShell 또는 터미널에서 실행
rustc --version
cargo --version

# 출력 예:
# rustc 1.75.0 (1d8b05fc5 2023-12-21)
# cargo 1.75.0 (ecb9851af 2023-10-18)
```

**설치되지 않았다면:**

```bash
# https://rustup.rs/ 방문 후 설치
# 또는 직접:
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

---

## 📁 파일 구조 설정

### Step 1: 폴더 생성

```bash
# PowerShell에서 실행
mkdir corezero-core
cd corezero-core

# 필요한 폴더 생성
mkdir src
mkdir src\audio
mkdir docs
```

### Step 2: 최종 구조

```
corezero-core/
├── src/
│   ├── main.rs
│   ├── audio/
│   │   ├── mod.rs
│   │   ├── engine.rs
│   │   ├── driver.rs
│   │   ├── metrics.rs
│   │   ├── ring_buffer.rs
│   │   └── scheduler.rs
│   └── utils/
│       └── (향후 추가)
├── docs/
│   └── (향후 추가)
├── Cargo.toml
├── README.md
├── LICENSE
└── .gitignore
```

---

## 📝 코드 복사

### outputs 폴더의 파일들:

이전 단계에서 제공된 10개 파일을 다음과 같이 복사하세요:

```
01_Cargo.toml → Cargo.toml
02_main.rs → src/main.rs
03_audio_mod.rs → src/audio/mod.rs
04_engine.rs → src/audio/engine.rs
05_driver.rs → src/audio/driver.rs
06_metrics.rs → src/audio/metrics.rs
07_ring_buffer.rs → src/audio/ring_buffer.rs
08_scheduler.rs → src/audio/scheduler.rs
09_README.md → README.md
10_gitignore → .gitignore
11_LICENSE → LICENSE
```

### 복사 방법 (Windows PowerShell)

```powershell
# 각 파일을 적절한 위치에 복사
Copy-Item "01_Cargo.toml" "Cargo.toml"
Copy-Item "02_main.rs" "src\main.rs"
Copy-Item "03_audio_mod.rs" "src\audio\mod.rs"
Copy-Item "04_engine.rs" "src\audio\engine.rs"
Copy-Item "05_driver.rs" "src\audio\driver.rs"
Copy-Item "06_metrics.rs" "src\audio\metrics.rs"
Copy-Item "07_ring_buffer.rs" "src\audio\ring_buffer.rs"
Copy-Item "08_scheduler.rs" "src\audio\scheduler.rs"
Copy-Item "09_README.md" "README.md"
Copy-Item "10_gitignore" ".gitignore"
Copy-Item "11_LICENSE" "LICENSE"
```

---

## 🔨 빌드

### Debug 버전 (빠른 컴파일)

```bash
cd corezero-core

# 의존성 다운로드 및 컴파일
cargo build

# 실행 파일 위치
# Windows: target\debug\corezero.exe
# Linux/macOS: target/debug/corezero
```

**시간:** 약 3-5분 (첫 빌드, 인터넷 속도에 따라)

### Release 버전 (최적화, 권장)

```bash
cargo build --release

# 실행 파일 위치
# Windows: target\release\corezero.exe
# Linux/macOS: target/release/corezero

# 파일 크기: ~5MB
```

**시간:** 약 10-15분 (최적화 때문)

### 빌드 성공 확인

```
Finished release [optimized] target(s) in XXs
```

---

## 🧪 테스트

### 테스트용 FLAC 파일 준비

**방법 1: 기존 FLAC 파일 사용**

```bash
# 당신의 FLAC 파일로 테스트
corezero "C:\Users\YourName\Music\song.flac"
```

**방법 2: 테스트용 작은 FLAC 만들기** (선택)

```bash
# ffmpeg 설치 필요:
# https://ffmpeg.org/download.html

# 짧은 테스트 FLAC 생성 (10초, 무음)
ffmpeg -f lavfi -i anullsrc=r=48000:cl=stereo -t 10 -q:a 9 test.flac
```

### Test 1: 기본 재생

```bash
# 단순 재생 (Ctrl+C로 중지)
.\target\release\corezero.exe "C:\path\to\music.flac"

# 출력 예:
# 🎵 CoreZero v1.0 - Zero-Underrun Audio Engine
# Starting audio playback...
# 🔊 Output device: Speakers (Realtek Audio)
#   Sample rate: 48000 Hz
#   Channels: 2
# ✅ Decoded 1152000 frames (24.00 sec)
# ▶️  Playing...
```

### Test 2: 실시간 메트릭 표시

```bash
.\target\release\corezero.exe "music.flac" --metrics

# 재생 중 메트릭이 수집됨
# Ctrl+C로 중지하면 최종 메트릭 표시
```

### Test 3: Stress Test (600초) - 중요!

```bash
# 이것이 "zero underruns" 증명입니다
.\target\release\corezero.exe "music.flac" --stress 600

# 약 10분 동안 음악이 반복 재생됨
# 완료되면 메트릭 출력:
#
# =================================================
#      🎵 CoreZero RT Metrics Report (600 sec)
# =================================================
#
#   Underruns:              0 ← 가장 중요!
#   Callback count:         59997
#   Max callback gap:       17.16 ms
#   Min buffer depth:       1048576 samples
#
#   ✅ ZERO UNDERRUNS - Perfect real-time performance!
#
# =================================================
```

### Test 4: CSV 내보내기

```bash
.\target\release\corezero.exe "music.flac" --stress 600 --csv-export metrics.csv

# metrics.csv 파일 생성됨
# 내용:
# Metric,Value
# Underruns,0
# Callback Count,59997
# Max Callback Gap (ns),17157500
# Max Callback Gap (ms),17.157500
# Min Buffer Depth (samples),1048576
```

---

## 📊 예상 결과

### 좋은 결과

```
✅ Underruns: 0
✅ Max gap: <20ms
✅ Min depth: >1000000 samples
✅ CPU: <10%
```

### 경고 (개선 필요)

```
⚠️ Underruns: >100
⚠️ Max gap: >50ms
⚠️ Min depth: <100000
⚠️ CPU: >30%
```

### 해결 방법

```
1. 백그라운드 프로그램 종료
2. 전원 절약 모드 비활성화
3. 고해상도 파일로 재시도 (48kHz 미만)
4. 다른 USB 포트 사용
5. Windows 업데이트 적용
```

---

## 🔧 문제 해결

### 에러 1: "No output device found"

**원인:** 오디오 출력 장치 없음

**해결:**
```bash
# 1. Windows 음량 설정 확인
#    - 설정 → 시스템 → 사운드
#    - 출력 장치 확인

# 2. 드라이버 업데이트
#    - 기기 관리자 → 사운드, 비디오 및 게임 컨트롤러
#    - 오디오 드라이버 업데이트

# 3. 다른 출력 장치 시도
```

### 에러 2: "Failed to open file"

**원인:** 파일을 찾을 수 없음

**해결:**
```bash
# 전체 경로 사용
.\target\release\corezero.exe "C:\Users\YourName\Music\song.flac"

# 또는 파일을 현재 폴더에 복사
copy "C:\path\to\song.flac" .\song.flac
.\target\release\corezero.exe "song.flac"
```

### 에러 3: Underruns 발생

**원인:** 시스템 오버로드

**해결:**
```bash
# 1. 백그라운드 프로그램 종료
#    - Chrome 종료
#    - Discord 종료
#    - Antivirus 일시 중지

# 2. 전원 설정
#    - 배터리 → 고성능 모드
#    - 전원 절약 모드 해제

# 3. 파일 위치
#    - SSD 사용 (HDD 피하기)
#    - 로컬 드라이브 (네트워크 드라이브 피하기)
```

### 에러 4: "Failed to build"

**원인:** Rust/Cargo 버전 문제

**해결:**
```bash
# Rust 업데이트
rustup update

# Clean build
cargo clean
cargo build --release
```

---

## 📈 다음 단계

### Phase 1 테스트 완료 후:

1. **GitHub 공개 준비**
   ```bash
   git init
   git add .
   git commit -m "CoreZero v1.0: Zero-underrun RT audio engine"
   git remote add origin https://github.com/yourusername/corezero
   git push -u origin main
   ```

2. **포럼 공개**
   - Head-Fi 포스트 작성
   - Reddit r/audiophile 공유
   - ASR 포스트

3. **피드백 수집**
   - GitHub Issues
   - 포럼 댓글
   - 개선점 정리

4. **Phase 1.5 개발**
   - DSD 지원 추가
   - ALSA Linux 지원
   - 버그 수정

---

## 💡 팁

### 테스트 최적화

```bash
# 최적 테스트 환경
1. 외장 USB 오디오 인터페이스 사용 (권장)
2. Wi-Fi 끄기
3. 다른 프로그램 모두 종료
4. 전원 유선 연결 (배터리 X)

# 이렇게 하면 Underruns: 0 확률 높음
```

### CPU 모니터링

```bash
# Windows Task Manager
Ctrl + Shift + Esc

# Linux
top
watch -n 1 'ps aux | grep corezero'

# 목표: CPU <10%
```

### 메모리 모니터링

```bash
# Windows
Get-Process | Where-Object {$_.Name -eq "corezero"} | Format-Table WorkingSet

# 목표: Memory <50MB
```

---

## ✅ 최종 체크리스트

- [ ] Rust 설치됨
- [ ] 파일 구조 완벽
- [ ] 모든 코드 파일 복사됨
- [ ] Cargo.toml 확인됨
- [ ] Release 빌드 성공
- [ ] Test 1: 기본 재생 OK
- [ ] Test 2: 메트릭 표시 OK
- [ ] Test 3: Stress test OK (Underruns: 0)
- [ ] Test 4: CSV 내보내기 OK
- [ ] GitHub 준비 완료

모든 항목 체크되면: **공개 준비 완료!** 🚀

---

**문제 있으면 GitHub Issues에 보고해주세요.**

Happy audio engineering! 🎵
