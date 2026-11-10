# B300 GPU 7 · vLLM 서빙 테스트

> 검증일: 2026-11-10  
> 서버: `b300` (8× NVIDIA B300 SXM6 PC, 275GB VRAM each)  
> GPU: **7번 (마지막 GPU)**  
> 모델: `Qwen3.5-27B`  
> vLLM: `0.32.0` (`/data/baryon-b300/.venv`)  
> 포트: `127.0.0.1:8020`

## 1. 구성

```text
Mac (Baryon AX full stack)
  └─ API 컨테이너 ── host.docker.internal:8020 ── SSH tunnel
                                                   │
B300 서버                                          ▼
  └─ vLLM 0.32.0 ── GPU 7 ── Qwen3.5-27B ── 127.0.0.1:8020
```

- 채팅 추론은 **B300 GPU 7의 vLLM**이 담당합니다.
- Qdrant/PostgreSQL/Redis/Langfuse/Mem0는 기존 full-stack 서비스를 그대로 사용합니다.
- Mem0의 메모리 추출은 로컬 Ollama(`qwen2.5:7b`)를 유지합니다. vLLM은 채팅 추론만 담당합니다.

## 2. vLLM 시작 (B300)

```bash
scp scripts/b300_serve_vllm.sh b300:/data/baryon-b300/scripts/
ssh b300 'mkdir -p /data/baryon-b300/logs && \
  tmux new-session -d -s baryon-vllm \
  "bash /data/baryon-b300/scripts/b300_serve_vllm.sh >> /data/baryon-b300/logs/serve-baryon-ax-vllm.log 2>&1"'

# 준비 확인 (최대 수 분 소요)
ssh b300 'curl -sf http://127.0.0.1:8020/v1/models | python3 -m json.tool | head'
ssh b300 'nvidia-smi -i 7 --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader'
```

스크립트는 다음을 확인한 뒤 실행합니다.

- GPU 7의 사용 메모리가 0 MiB인지
- 8020 포트가 비어 있는지
- `Qwen3.5-27B` 모델 존재 여부
- `CUDA_VISIBLE_DEVICES=7`, tensor parallel 1, max-model-len 32768, gpu-memory-utilization 0.90

로그: `/data/baryon-b300/logs/serve-baryon-ax-vllm.log`

## 3. SSH 터널 (Mac)

vLLM은 B300의 loopback에만 바인딩됩니다. Mac의 Docker Desktop에서 접근하려면 터널을 엽니다.

```bash
scripts/b300_vllm_tunnel.sh &
curl -s http://127.0.0.1:8020/v1/models
```

- 터널은 `0.0.0.0:8020`에 바인딩됩니다. 신뢰된 네트워크에서만 사용하고, 테스트 후 종료하세요.
- Docker 컨테이너는 `http://host.docker.internal:8020`으로 접근합니다.

## 4. 플랫폼을 B300 vLLM에 연결

```bash
OLLAMA_HOST_URL=http://host.docker.internal:11435 \
MODEL_HOST_URL=http://host.docker.internal:8020 \
MODEL_NAME=baryon-ax-qwen \
MODEL_DISABLE_THINKING=true \
MEM0_LLM_MODEL=qwen2.5:7b \
  docker compose -f docker-compose.yml -f docker-compose.full.yml \
  --profile deps up -d --build

# 또는 B300 전용 override 사용
docker compose -f docker-compose.yml -f docker-compose.full.yml \
  -f docker-compose.b300.yml --profile deps up -d --build
```

- `MODEL_HOST_URL`은 `docker-compose.full.yml`의 `MODEL_BASE_URL`로 전달됩니다.
- `MODEL_NAME`은 vLLM의 `--served-model-name`과 일치해야 합니다.
- `MODEL_DISABLE_THINKING=true`는 Qwen3.5의 thinking 출력을 끄고 최종 답변만 받기 위한 설정입니다. API가 `chat_template_kwargs: {"enable_thinking": false}`를 전송합니다.
- `MEM0_LLM_MODEL`은 `${MODEL_NAME}`과 분리되어 로컬 Ollama 모델을 유지합니다.

확인:

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
curl -s http://127.0.0.1:8000/v1/models
```

`/v1/models`에 `baryon-ax-qwen`이 보이면 연결된 것입니다.

## 5. 검증 결과

| 항목 | 결과 |
| --- | --- |
| vLLM 모델 | `baryon-ax-qwen` (Qwen3.5-27B) |
| GPU 7 메모리 | 약 246,030 MiB / 275,040 MiB |
| 비스트리밍 채팅 | 약 **0.50초** |
| 스트리밍 채팅 | 약 **0.58초**, 39 토큰 |
| Qdrant 출처 | ✅ `b300-vllm-doc.md` |
| PostgreSQL 감사 | ✅ `model=baryon-ax-qwen` |
| Langfuse trace | ✅ latency ≈ 0.53초, user `b300-user` |
| Full-stack smoke | ✅ ALL CHECKS PASSED |
| Playwright E2E | ✅ 11 checks passed |
| API tests | ✅ 21 passed |

실제 답변 예시:

```text
B300 서버는 8 번째 GPU(GPU 7)를 사용하여 Qwen3.5-27B 모델을 vLLM으로 서빙합니다.
```

## 6. 종료

```bash
# vLLM (tmux로 시작한 경우)
ssh b300 'tmux kill-session -t baryon-vllm'

# 현재 실습에서 setsid로 시작한 프로세스인 경우 (명령줄 확인 후 종료)
ssh b300 'pgrep -af "vllm serve.*8020"'

# Mac SSH 터널
pkill -f 'ssh -N -L 0.0.0.0:8020'
```

종료 후 GPU 7이 0 MiB로 돌아왔는지 확인하세요.

```bash
ssh b300 'nvidia-smi -i 7 --query-gpu=memory.used --format=csv,noheader'
```

## 7. 주의사항

- Qwen3.5는 기본적으로 thinking process를 출력할 수 있습니다. 플랫폼에서는 `MODEL_DISABLE_THINKING=true`를 사용합니다.
- vLLM은 B300 loopback에만 열고, SSH 터널로 접근하는 구성을 권장합니다.
- 27B BF16 모델은 GPU 7 한 장에 충분히 들어가지만, `gpu-memory-utilization=0.90`으로 KV cache를 크게 잡습니다. 다른 작업이 GPU 7을 사용해야 하면 `gpu-memory-utilization`을 낮추세요.
- vLLM 버전과 모델 아키텍처(`Qwen3_5ForConditionalGeneration`)가 맞지 않으면 `--trust-remote-code` 또는 이미지/버전 조정이 필요할 수 있습니다.
- 이 테스트는 **추론 서버만 B300**에 두고, 플랫폼 데이터 서비스는 Mac의 full stack을 사용했습니다. B300에 전체 플랫폼을 배포하려면 `docs/full-stack.md`의 Compose 구성을 B300 포트/네트워크에 맞게 조정하세요.
