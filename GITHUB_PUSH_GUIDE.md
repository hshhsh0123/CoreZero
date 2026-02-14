# CoreZero: GitHub 푸시 완전 가이드 (5분)

## 📋 준비사항

### 1단계: GitHub 계정 확인

- GitHub.com 로그인됨?
- SSH key 또는 Personal Access Token 준비?

**아니면 HTTPS 사용 (더 쉬움)**

---

## 🚀 실행 (5분 소요)

### Step 1: 프로젝트 폴더로 이동

```powershell
cd C:\Users\123hs\Desktop\corezero
```

### Step 2: Git 초기화

```powershell
git init
```

**출력:**
```
Initialized empty Git repository in C:\Users\123hs\Desktop\corezero\.git\
```

### Step 3: 모든 파일 추가

```powershell
git add .
```

### Step 4: 첫 커밋

```powershell
git config user.name "Your Name"
git config user.email "your.email@example.com"

git commit -m "CoreZero v1.0: Zero-underrun RT audio engine

- Proven underruns: 0 @600sec stress test
- Lock-free SPSC ring buffer design
- Real-time metrics collection
- MIT License - Open source
- Bit-perfect audio playback (FLAC, WAV, MP3)
- Cross-platform (Windows, Linux, macOS)

Metrics (verified):
- Underruns: 0
- Max callback gap: 22.78ms
- Buffer depth: stable
- CPU usage: <5% @48kHz"
```

### Step 5: GitHub에 새 저장소 생성

1. **브라우저에서 GitHub.com 방문**
2. **"New repository" 클릭** (또는 우측 상단 + 아이콘)
3. **다음 정보 입력:**

```
Repository name: corezero
Description: Open-Source Zero-Underrun RT Audio Engine
Visibility: Public ← 중요!
Initialize this repository: NO (체크 해제)
```

4. **"Create repository" 클릭**

5. **나타나는 페이지에서 다음 명령어를 복사하되, 아래 내용을 사용하세요:**

### Step 6: Remote 추가 및 Push

**YOUR_USERNAME을 당신의 GitHub username으로 바꾸세요:**

```powershell
# Remote 추가
git remote add origin https://github.com/YOUR_USERNAME/corezero.git

# 기본 브랜치를 main으로 변경 (GitHub 기본값)
git branch -M main

# Push! (최초 1회)
git push -u origin main
```

**USERNAME 찾기:**
- GitHub.com 로그인 → 프로필 아이콘 → "Your profile"
- URL에서: `github.com/YOUR_USERNAME`

### Step 7: GitHub에서 확인

1. **브라우저에서 `github.com/YOUR_USERNAME/corezero` 방문**
2. **모든 파일이 보이면 성공!** ✅

---

## 📝 커밋 메시지 예시 (위에서 사용)

```
CoreZero v1.0: Zero-underrun RT audio engine

- Proven underruns: 0 @600sec stress test
- Lock-free SPSC ring buffer design
- Real-time metrics collection
- MIT License - Open source
- Bit-perfect audio playback (FLAC, WAV, MP3)
- Cross-platform (Windows, Linux, macOS)

Metrics (verified):
- Underruns: 0
- Max callback gap: 22.78ms
- Buffer depth: stable
- CPU usage: <5% @48kHz
```

---

## 🔑 인증 방법

### HTTPS (권장, 더 쉬움)

```powershell
git push -u origin main

# 첫 실행 시:
# Username: YOUR_GITHUB_USERNAME
# Password: YOUR_PERSONAL_ACCESS_TOKEN (또는 GitHub password)
```

**Personal Access Token 생성 (필요시):**
1. GitHub Settings → Developer settings → Personal access tokens
2. "Generate new token"
3. Scope: `repo` (전체 선택)
4. 생성된 token 복사
5. Password 입력 시 token 붙여넣기

### SSH (고급)

```powershell
git remote set-url origin git@github.com:YOUR_USERNAME/corezero.git
git push -u origin main
```

**SSH key 설정이 필요합니다** (생략)

---

## ✅ 완료 확인

### GitHub 페이지 확인

```
https://github.com/YOUR_USERNAME/corezero

보이는 것:
✅ corezero 폴더 (빨간 박스)
✅ src/ 폴더
✅ Cargo.toml
✅ README.md
✅ LICENSE
✅ .gitignore
```

### README 확인

- GitHub 페이지에 README.md가 자동으로 표시됨
- "Zero-underrun RT audio engine" 텍스트 보임

---

## 🚨 문제 해결

### 에러: "fatal: not a git repository"

```powershell
# 올바른 폴더?
cd C:\Users\123hs\Desktop\corezero
pwd  # 현재 경로 확인

# 다시 시도
git init
git add .
git commit -m "..."
```

### 에러: "authentication failed"

```powershell
# HTTPS 대신 SSH 사용
git config --global credential.helper store
git push -u origin main

# 또는 token 재생성
```

### 에러: "branch main not found"

```powershell
# 로컬 branch 확인
git branch

# 없으면 생성
git checkout -b main
git push -u origin main
```

### 에러: "remote origin already exists"

```powershell
# 기존 remote 제거
git remote remove origin

# 다시 추가
git remote add origin https://github.com/YOUR_USERNAME/corezero.git
git push -u origin main
```

---

## 📊 최종 체크리스트

- [ ] GitHub.com 로그인됨
- [ ] `cd C:\Users\123hs\Desktop\corezero` 실행
- [ ] `git init` 완료
- [ ] `git add .` 완료
- [ ] `git commit -m "..."` 완료
- [ ] GitHub에서 새 저장소 생성됨
- [ ] `git remote add origin https://...` 실행
- [ ] `git branch -M main` 실행
- [ ] `git push -u origin main` 실행
- [ ] GitHub 페이지에서 파일 확인됨

---

## 🎯 완료 후

### 1. GitHub 링크 얻기

```
https://github.com/YOUR_USERNAME/corezero
```

### 2. 포럼에 공유

**Head-Fi 포스트:**
```
CoreZero: Open-Source Zero-Underrun Audio Engine

🎵 What: Real-time audio player in Rust
📊 Proof: Underruns: 0 @600sec verified
💾 Size: 5MB, <5% CPU
📄 License: MIT (free & open-source)

GitHub: https://github.com/YOUR_USERNAME/corezero

Features:
✅ Zero underruns proven
✅ FLAC/WAV/MP3 support
✅ Real-time metrics
✅ Cross-platform
✅ Source code verification possible

Download & build from source!
```

### 3. Reddit 공유

**r/audiophile:**
```
Title: "I built an open-source audio player. Underruns: 0. (Proof inside)"

I spent 2 months on this. It's proven zero underruns work.

GitHub: https://github.com/YOUR_USERNAME/corezero

[메트릭 스크린샷]
```

---

## 🚀 명령어 한 줄씩 복사

### 복사할 명령어들 (순서대로)

```powershell
# 1. 폴더 이동
cd C:\Users\123hs\Desktop\corezero

# 2. Git 초기화
git init

# 3. 파일 추가
git add .

# 4. 설정
git config user.name "Your Name"
git config user.email "your.email@example.com"

# 5. 커밋
git commit -m "CoreZero v1.0: Zero-underrun RT audio engine"

# 6. Remote 추가 (YOUR_USERNAME 바꾸기!)
git remote add origin https://github.com/YOUR_USERNAME/corezero.git

# 7. Branch 변경
git branch -M main

# 8. Push!
git push -u origin main
```

---

## 💡 팁

### Git 처음 사용?

```powershell
# 전역 설정 (한 번만)
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# 이후로는 각 프로젝트마다 설정 불필요
```

### 이미 Git 설정했으면

```powershell
# 프로젝트 폴더에서만
cd C:\Users\123hs\Desktop\corezero

# 1. 초기화부터
git init
git add .
git commit -m "CoreZero v1.0"
```

---

## ✨ 완료!

모든 명령어 실행 후:

```
✅ GitHub에 corezero 저장소 생성됨
✅ 모든 파일 업로드됨
✅ Public 저장소 (누구나 볼 수 있음)
✅ MIT License (자유로운 사용)
✅ 포럼에 공유 가능
```

---

**이제 `YOUR_USERNAME`을 당신의 GitHub username으로 바꾸고 명령어를 실행하면 됩니다!** 🚀

5분 안에 완료! 🎉
