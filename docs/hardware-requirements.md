# 하드웨어 최소사양

Baryon AX를 실행하기 위한 최소·권장 사양입니다. 구성은 **Basic Core**, **Full stack(외부 서비스 포함)**, **GPU 추론** 세 가지로 나눠 설명합니다.

> 아래 수치는 기본 문서 수천 건, 동시 사용자 5~20명, 32k 컨텍스트 이하를 가정한 **출발점**입니다. 문서량, 첨부 파일 크기, 동시 요청 수, 컨텍스트 길이에 따라 RAM·VRAM·디스크가 증가합니다. 실제 도입 전에는 [B300 배포 문서](b300-host-deploy.md)의 측정값을 참고해 자체 부하 테스트를 수행하세요.

## 1. 한눈에 보는 최소사양

| 구성 | CPU | RAM | GPU | 디스크 | 용도 |
| --- | --- | --- | --- | --- | --- |
| **Basic Core** | 2 vCPU | 4 GB | 불필요 | 10 GB | 데모 모델 + 파일 저장. 기능 확인·개발 |
| **Full stack (CPU)** | 8 vCPU | 16 GB (권장 32 GB) | 불필요 | 100 GB (권장 500 GB) | Qdrant·PostgreSQL·Redis·Langfuse·Mem0 + CPU 추론 |
| **Full stack + 7B GPU** | 8 vCPU | 32 GB | 24 GB VRAM | 250 GB | 소규모 사내 파일럿 (Ollama 7B) |
| **Production + 27B GPU** | 16 vCPU | 64 GB | **80 GB VRAM** (권장 96 GB+) | 500 GB NVMe | 부서 단위 운영 (vLLM Qwen3.5-27B) |
| **Production + 122B GPU** | 32 vCPU | 256 GB | **288 GB VRAM** 또는 4×80 GB | 1 TB+ NVMe | 전사 규모·고품질 추론 (122B-A10B) |

- **네트워크**: 사내 LAN 1 Gbps 이상. 외부 인터넷은 선택 사항이며 폐쇄망에서 동작합니다.
- **운영체제**: Linux x86_64 (Ubuntu 22.04/24.04 LTS 권장). macOS는 개발·데모용입니다.
- **디스크**: 모델 가중치와 벡터 인덱스를 위해 SSD/NVMe를 권장합니다. HDD는 검색 지연이 커집니다.

## 2. GPU별 모델 구성

| 모델 | 파라미터 | BF16 가중치 | 최소 VRAM | 권장 VRAM | 비고 |
| --- | --- | --- | --- | --- | --- |
| Qwen2.5-7B | 7B | 약 14 GB | 24 GB | 24~32 GB | RTX 4090/5090, L4 24GB |
| Qwen3.5-27B | 27B | 약 54 GB | **80 GB** | 96~141 GB | A100/H100 80GB는 32k 컨텍스트에서 여유가 작음 |
| Qwen3.5-122B-A10B | 122B (A10B) | 약 244 GB | **288 GB** | 288 GB+ | B300 1장에서 8k 컨텍스트로 검증 (약 268 GB 사용) |

### VRAM 산정 기준

```text
필요 VRAM ≈ 모델 가중치 + KV 캐시 + 활성화/워크스페이스
```

- **27B BF16**: 가중치 54 GB + 32k 컨텍스트 KV 캐시 10~20 GB + 여유분 → 80 GB GPU는 최소선, 96 GB 이상을 권장합니다.
- **122B-A10B BF16**: 가중치 244 GB + KV 캐시 → 288 GB GPU 1장 또는 80 GB GPU 4장(TP4)이 필요합니다.
- **4-bit 양자화**를 쓰면 27B를 24~48 GB GPU에 올릴 수 있지만, 품질·지연·지원 범위가 달라지므로 기본 구성으로 권장하지 않습니다.
- vLLM의 `--gpu-memory-utilization`은 **예약 비율**입니다. B300에서 27B를 `0.90`으로 실행하면 실제 가중치보다 큰 약 246 GB가 예약됩니다. 다른 작업과 GPU를 공유할 때는 `0.30`처럼 낮춰 예약량을 줄입니다.

## 3. 서비스별 자원 예산

| 구성 요소 | 최소 RAM | 권장 RAM | 디스크 | 비고 |
| --- | --- | --- | --- | --- |
| API (FastAPI + 임베딩 + Mem0) | 2 GB | 4 GB | 1 GB | FastEmbed 모델 약 0.5 GB 포함 |
| Web (Nginx + 정적 자산) | 128 MB | 256 MB | 200 MB | 빌드 시 Node 22 필요 |
| Qdrant | 2 GB | 8 GB | 인덱스 크기의 2~3배 | 벡터·페이로드·스냅샷 |
| PostgreSQL | 1 GB | 4 GB | 감사 로그 크기 + 백업 | 감사 이벤트·Langfuse DB |
| Redis | 256 MB | 1 GB | 1 GB | 레이트 리밋·캐시 |
| Langfuse | 2 GB | 4 GB | 20 GB+ | 트레이스 보존 기간에 비례 |
| vLLM (호스트) | 16 GB | 모델 크기와 동일 | 모델 가중치 | 27B는 CPU RAM 64 GB 이상 권장 |
| OS + Docker | 4 GB | 8 GB | 30 GB | 이미지·로그·임시 파일 |

> API와 Mem0는 같은 컨테이너에서 동작합니다. 메모리 추출 모델을 별도 GPU/서버에 두면 API RAM을 줄일 수 있습니다.

## 4. 스토리지 산정

| 항목 | 27B 구성 예시 | 122B 구성 예시 |
| --- | --- | --- |
| OS + Docker 이미지 | 30 GB | 30 GB |
| 모델 가중치 | 55 GB | 245 GB |
| 임베딩 캐시 | 1 GB | 1 GB |
| 문서 원본 + 파싱 텍스트 | 50 GB | 100 GB |
| Qdrant 인덱스 | 20 GB | 50 GB |
| PostgreSQL + 감사 로그 | 20 GB | 50 GB |
| Langfuse 트레이스 | 20 GB | 50 GB |
| 백업·여유 공간 | 100 GB | 200 GB |
| **합계(권장)** | **약 300 GB** | **약 750 GB** |

- 문서 1만 건(평균 200 KB, PDF·HWP·이미지 포함) 기준 약 2~5 GB의 원본이 추가됩니다.
- 원본 파일은 파싱 텍스트와 함께 보관되므로 문서량에 비례해 증가합니다.
- 감사 로그와 Langfuse 트레이스는 보존 기간을 정하고 별도 파티션·백업 정책을 적용하세요.

## 5. 소프트웨어 요구사항

| 항목 | 최소 | 권장 |
| --- | --- | --- |
| OS | Linux x86_64 | Ubuntu 22.04/24.04 LTS |
| Docker | 24.x | 27.x+ |
| Docker Compose | v2.20 | v2.30+ |
| NVIDIA 드라이버 | 550.x | 570.x+ |
| CUDA | 12.4 | 12.8+ |
| Python (개발) | 3.12 | 3.12 |
| Node.js (웹 빌드) | 22 | 22 LTS |
| 브라우저 | 최신 Chrome/Edge/Safari | 최신 Chrome |

- GPU 추론은 NVIDIA 드라이버와 컨테이너 런타임(`nvidia-container-toolkit`)이 필요합니다.
- CPU 추론만 사용할 경우 GPU·CUDA 요구사항은 없지만, 7B 모델도 실사용 속도를 내기 어렵습니다.
- 폐쇄망은 이미지·모델·패키지를 사전 미러링해야 합니다. [에어갭 가이드](features.md)를 참고하세요.

## 6. 포트와 네트워크

| 서비스 | 기본 포트 | 노출 범위 |
| --- | --- | --- |
| Web UI | 3001 (B300: 3101) | 사내 사용자 |
| API | 8000 (B300: 8100) | Web에서만 |
| Qdrant | 6333/6334 (B300: 6433/6434) | API에서만 |
| PostgreSQL | 5433 (B300: 5533) | API에서만 |
| Redis | 6379 (B300: 6479) | API에서만 |
| Langfuse | 3000 (B300: 3102) | 관리자 |
| vLLM | 8020 (loopback) | API 서버에서만 |

- 운영 환경에서는 데이터 서비스 포트를 외부에 공개하지 말고 `127.0.0.1` 또는 사설 네트워크에 바인딩하세요.
- 원격 GPU 서버를 쓸 경우 SSH 터널 또는 사설 VPN을 사용합니다.

## 7. 구성 예시

### 개발·데모 (Mac/노트북)

```text
CPU 4코어 · RAM 8 GB · 디스크 20 GB · GPU 없음
→ Basic Core, 데모 모델, 문서 수십 건
```

### 소규모 사내 파일럿

```text
CPU 8코어 · RAM 32 GB · 디스크 250 GB SSD · GPU 24 GB (RTX 4090/L4)
→ Full stack + Ollama 7B, 동시 사용자 5~10명
```

### 부서 운영 (권장)

```text
CPU 16코어 · RAM 64 GB · 디스크 500 GB NVMe · GPU 80~96 GB (A100/H100)
→ vLLM 27B + Qdrant + PostgreSQL + Redis + Langfuse + Mem0
```

### 전사·고품질 추론

```text
CPU 32코어 · RAM 256 GB · 디스크 1 TB+ NVMe · GPU 288 GB (B300) 또는 4×80 GB
→ vLLM 122B-A10B, 8k~32k 컨텍스트
```

## 8. 주의사항

- 위 수치는 **보장된 성능이 아니라 출발점**입니다. 실제 사용량은 문서 수, 첨부 파일, 동시 요청, 컨텍스트 길이에 크게 좌우됩니다.
- 컨텍스트를 32k에서 128k로 늘리면 KV 캐시가 수배로 증가해 VRAM이 부족해질 수 있습니다.
- Qdrant와 PostgreSQL은 백업·스냅샷 공간을 별도로 확보하세요.
- GPU를 다른 학습 작업과 공유할 때는 vLLM `gpu-memory-utilization`을 낮추고, OOM이 발생하지 않는지 확인해야 합니다.
- 폐쇄망에서는 모델 가중치(수십~수백 GB)를 USB/내부 미러로 반입할 시간과 공간을 계획에 포함하세요.

## 관련 문서

- [B300 호스트 배포](b300-host-deploy.md)
- [B300 GPU 7 vLLM 테스트](b300-vllm.md)
- [전체 기능 목록](features.md)
- [보안 기준](security.md)
