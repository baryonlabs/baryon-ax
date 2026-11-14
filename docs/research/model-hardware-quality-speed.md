> 이 문서는 [모델·하드웨어 선택 가이드](../model-hardware-selection.md)의 조사 배경입니다. 공개 자료·벤더 발표·커뮤니티 측정이 섞여 있고 일부 수치는 추정이므로, 계약·도입 판단에는 요약 문서와 자체 평가를 우선하세요. 출처 접속일: 2026-11-14.

# 모델·하드웨어 품질/속도 조사 노트

결론부터 말하면, 2026년 11월 기준 Baryon AX가 8개 하드웨어 구간에 낼 답은 **Qwen3.5 계열(Apache 2.0) 한 줄기**이며, 정밀도 기본값은 **FP8**, 각 구간의 1순위는 **CPU 9B/35B-A3B · 24GB 9B FP8 · 48GB 27B FP8 · 80GB 27B BF16 · 141GB 122B-A10B INT4 · 288GB 122B-A10B FP8 · 4×80GB 122B FP8 TP4 · 8×80GB 122B FP8 복제 2개(품질 상한 397B FP8)** 다. 이 배치는 모델 순위가 아니라 **VRAM 회계**에서 나온다—총 가중치가 한 노드에 상주하고 32K KV를 남기는 가장 큰 모델이 이기며, 그 다음 병목은 prefill이다. 한국어 품질만 보면 EXAONE 4.5가 앞서지만 라이선스가 상업 사용과 **출력물**까지 금지하므로 제품 기본값이 될 수 없고, Solar Open 2와 A.X K2는 라이선스·메모리 확인 전에는 견적에 넣을 수 없다. 저장소가 실제로 검증한 것은 **Qwen3.5-27B BF16(32K)과 122B-A10B BF16(8K)의 B300 단일 GPU 구성**뿐이며, 나머지 tok/s와 동시성은 대역폭 산술과 인접 측정에서 유도한 추정이다. 141GB H200에서 122B FP8이 계획치 147GB로 **약 6GB(5.6GiB) 모자라** 한 장에 올라가지 않는 함정, 24GB에서 35B INT4가 KV를 3GB만 남기는 함정, 8×80GB에서 397B보다 122B 복제가 TCO상 유리할 수 있다는 점이 실무적 핵심이다.

> 모든 출처 URL은 **2026-11-14** 접속했다. 표의 **측**=공개 벤치마크·기기 로그 측정, **산**=스펙·공식 산술, **추**=대역폭 비례·인접 측정에서 유도한 추정이다. 별도 표기가 없는 GB/GiB·슬롯 산술은 **산**으로 본다. 저장소 수치는 [hardware-requirements.md](../docs/hardware-requirements.md)와 [b300-vllm.md](../docs/b300-vllm.md)의 2026-11-10 검증 로그를 인용했다.

## 티어를 가르는 두 숫자: 가중치 상주량과 32K KV 예산

파라미터 수는 티어를 가르지 않는다. 122B-A10B는 10B만 활성화하므로 **디코드 속도는 10B 모델처럼** 나오지만, 122B 전체 가중치가 VRAM에 상주해야 하고 KV 캐시는 122B급으로 잡아야 한다. 실무 공식은 하나다: **`총 가중치 × 1.15 + (목표 동시 슬롯 × 32K × 토큰당 KV 바이트) ≤ 가용 VRAM`**. 이 식에 8개 구간을 넣으면 24GB→9B FP8, 48GB→27B FP8, 80GB→27B BF16, 141GB→122B INT4, 288GB→122B FP8, 4×80GB→122B FP8 TP4, 8×80GB→122B FP8 복제가 각 구간의 최대값으로 떨어진다. KV가 필요조건이고, 실제 병목은 그 다음의 **prefill 연산**이다. 32 vCPU에서 3B 모델을 128 동시로 돌린 측정이 집계 620 tok/s를 냈지만 P95 TTFT는 4.7초, 사용자당 5 tok/s 미만이었다 — CPU·저전력 구간에서 "느린 디코드"보다 "긴 TTFT"가 먼저 문제가 된다 ([Arijit Desai, 2026-11-04](https://ardecode.github.io/llm/inference/benchmarking/2026/11/04/vllm-cpu-inference-granite-rapids/)).

아래는 모델별 실제 크기다. **vLLM 계획치**는 공식 레시피의 `vram_minimum_gb`(BF16·FP8·INT4)이며, 이 값은 "그 정밀도로 그 컨텍스트를 서빙할 때의 보수적 예산"이지 순수 가중치 파일 크기가 아니다. 이 구분을 놓치면 141GB H200에 122B FP8을 넣는 사고가 난다.

| 모델 (총/활성) | BF16 | FP8 | INT4·NVFP4 | vLLM 계획치 (BF16·FP8·INT4) | 비고 |
|---|---|---|---|---|---|
| Qwen3.5-9B (9B) | 18GB | 11GB | 8GB | 22 · 11 · 8GB | 262K native, 24GB에서는 32~64K |
| Qwen3.5-27B (27B dense) | 54GB | 28GB | 15~17GB | 65 · 33 · 17GB | 262K native, 저장소 32K 검증 |
| Qwen3.5-35B-A3B (36B/3B) | 84GB | 42GB | 21GB | 84 · 42 · 21GB | 262K, 24GB 공식 최소 |
| Qwen3.5-122B-A10B (122B/10B) | 244~293GB | 122~147GB | 74GB | 293 · 147 · 74GB | 262K, 저장소 8k에서 262GiB 사용 |
| Qwen3.5-397B-A17B (397B/17B) | 953GB | 477GB | 238GB | 953 · 477 · 238GB | 262K, 8×80GB 이상 |
| Qwen3.8-27B (27B dense) | 55.6GB | 28GB | 24.6GiB (NVFP4) | 27B급 65 · 33 · 17GB(추) | hybrid attention, 262K/1M |

핵심은 **"계획치 ≠ 실측"** 이다. 저장소는 B300 한 장(275,040MiB=268.6GiB)에서 122B-A10B BF16을 8K로 올려 **268,560MiB(262GiB)** 를 썼고, 이는 244GB 가중치(227GiB)에 KV·활성화가 더해진 값이다 ([b300-vllm.md](../docs/b300-vllm.md)). 반면 vLLM 레시피의 293GB 계획치는 288GB 카드의 십진 표기보다 커서 "안 들어간다"고 오해하게 만든다. 27B도 마찬가지다. 저장소 로그의 **246,030MiB는 모델이 쓴 양이 아니라 `gpu-memory-utilization 0.90`이 예약한 양**이다. 27B BF16 가중치는 54GB(50GiB)에 불과하므로, 이 숫자로 모델 크기를 역산하면 안 된다.

단위 함정도 명시해 둔다. **B300 288GB(십진) ≈ 268.6GiB = 275,040MiB** 이고, H200 141GB = 131.3GiB, H100 80GB = 74.5GiB다. 122B FP8의 명목 가중치 122GB(113.6GiB)는 H200에 이론상 들어가지만 남는 KV가 17GiB뿐이라 32K 동시 1~2명이고, vLLM 계획치 147GB(136.9GiB)는 5.6GiB 부족해 기동 자체가 실패한다. 그래서 141GB의 답은 "FP8을 어떻게든"이 아니라 **INT4로 내려가 KV를 사는 것**이다 ([vLLM recipe variants](https://recipes.vllm.ai/Qwen/Qwen3.5-122B-A10B)).

## 1순위는 Qwen3.5 한 줄기, 24GB만 거래가 다르다

아래 표가 이 보고서의 결론이다. 동시 슬롯은 **32K RAG 프롬프트(2~4K 입력 + 300~600 출력)** 기준이며, 활성 사용자는 MillstoneAI가 제시한 **슬롯 × 3~5** 환산을 적용했다 ([MillstoneAI](https://cdn.millstoneai.com/benchmarks/qwen3-5-397b-a17b-fp8-8x-rtx-pro-6000-blackwell/qwen3-5-397b-a17b-fp8-8x-rtx-pro-6000-blackwell.pdf)). 괄호 안이 활성 사용자다.

| 하드웨어 | 1순위 | 대안 | 정밀도 | 컨텍스트 | 동시 슬롯 (활성) | 단일 tok/s | 집계 tok/s |
|---|---|---|---|---|---|---|---|
| **CPU** 2×EPYC 9004/Xeon 6, 512GB+ | **Qwen3.5-9B**(대화) / **35B-A3B**(배치) | Gemma 4 12B · Qwen3.5-4B | INT8/Q8 · INT4 | 8~16K | 4~8 (15~30) | 15~60추 | 60~150추 |
| **1×24GB** 4090/L4 | **Qwen3.5-9B** | 35B-A3B INT4 · Gemma 4 12B Q4 | FP8 | 32~64K | 4~6 (12~30) | 60~80(4090)·20~30(L4)추 | 300~500추 |
| **1×48GB** L40S/A6000 | **Qwen3.5-27B** | Gemma 4 31B FP8 · EXAONE 4.5 33B(연구) | FP8(L40S) · INT8(A6000) | 32~128K | 3~5 (9~25) | 25~35추 | 150~350추 |
| **1×80GB** H100/A100 | **Qwen3.5-27B** | 35B-A3B FP8(262K) · Gemma 4 31B BF16 · EXAONE 32B BF16 | BF16 | 32K | 3~5 (9~25) | 35~55(H100)·25~40(A100)추 | 400~900추 |
| **1×141GB** H200 | **Qwen3.5-122B-A10B** | 35B-A3B BF16(262K) · 27B BF16(대형 KV) | GPTQ-Int4/AWQ | 128~256K | 12~25 (36~125) | 40~70추 | 800~1,600추 |
| **1×288GB** B300 | **Qwen3.5-122B-A10B** | 122B BF16(검증·8K) · 397B-A17B NVFP4(품질) | **FP8** | **262K** | 20~40 (60~200) | 50~90추 | 1,200~2,500추 |
| **4×80GB** (320GB) | **Qwen3.5-122B-A10B TP4** | 122B BF16 TP4 · 397B INT4 TP4 · 27B×4 복제 | FP8 | 262K | 30~60 (90~300) | 70~110추 | 800~1,500추 |
| **8×80GB** (640GB) | **122B-A10B FP8 ×2 복제** | **397B-A17B FP8**(품질 상한) · 675B/V4(비한국어) | FP8 | 262K | 50~80 (150~400) | 70~90측* | 1,300~1,800측 |

> 동시 슬롯은 KV 예산에서 계산한 **산출값**이며, 컨텍스트 열은 **최대 단일 요청 길이**다(슬롯은 32K·**KV fp8** 기준, BF16 KV면 절반). 4×80GB의 집계 800~1,500은 8×H100 실측 1,585의 절반에서 유도했고, 8×80GB의 1,300~1,800은 **397B FP8 실측**이다. `*` 단일 스트림 70~90은 8×H200이 아니라 **8×RTX Pro 6000 측정(86.1→70.1 tok/s)** 에서 가져온 유사 구성 값이다 ([MillstoneAI](https://cdn.millstoneai.com/benchmarks/qwen3-5-397b-a17b-fp8-8x-rtx-pro-6000-blackwell/qwen3-5-397b-a17b-fp8-8x-rtx-pro-6000-blackwell.pdf)). 122B FP8 복제 2개를 택하면 GPU당 처리량이 더 높아 집계는 **1,600~2,000추**로 올라간다. CPU·24GB·48GB·141GB의 tok/s는 모두 추정이다. 공개된 27B 8bit H100 측정은 76/589/1,610(동시 1/10/50)이고 ([Winder.AI](https://winder.ai/vllm-vs-ollama-vs-sglang-llm-inference-comparison/)), 24GB 9B의 338 tok/s는 **배치 처리량으로 보고된 값**이라 단일 스트림 근거로 쓰지 않았다(아래 24GB 항목 참조).

**단일 GPU 사슬은 "한 장에 무엇이 상주하는가"로만 읽으면 된다.** 24GB에서 9B FP8을 고른 이유는 성능표가 아니라 거래다. 9B FP8은 약 10.4GiB 가중치를 남기고 KV를 같은 크기(166,074토큰)로 확보해 32K에서 4~6슬롯(16K에서는 8~10)을 만든다. 원문은 24GB 카드에서 FP8 338 tok/s·KV 166,074토큰을 보고했지만, 338은 **배치 처리량**이고 원문의 배수 표기(1.76×)와 실제 비율(2.9×)도 맞지 않아 단일 스트림 값으로 쓰지 않는다 ([ComputingForGeeks, 2026-09-30](https://computingforgeeks.com/gguf-vs-awq-vs-gptq/)). 대역폭 산술로 4090은 **60~80 tok/s(추)**, L4(약 300GB/s)는 **20~30 tok/s(추)** 다. 반대로 35B-A3B INT4는 21GB를 써서 KV가 3GB만 남고, 8~16K에서 1~2명이 한계다. 지식 커버리지가 품질을 지배하는 짧은 규정 QA라면 35B INT4가 낫지만, 그것은 "24GB 운영"이 아니라 "24GB 데모"다. 32GB 카드(5090)라면 예외적으로 27B NVFP4가 들어가며, 레시피 실측으로 `--enforce-eager` + `--max-num-seqs 8`에서 KV 152,917토큰(32K×4)이 확보된다 ([Qwen3.8-27B recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)).

48GB와 80GB는 같은 27B라도 정밀도가 다르다. 48GB에서는 BF16(54GB)이 물리적으로 안 들어가므로 **FP8 28GB + KV 15GiB**가 유일한 균형점이다. 단 FP8은 L40S(Ada)의 선택지이고, RTX A6000(Ampere)은 FP8 실리콘이 없어 W8A16으로 로드되므로 **A6000에는 INT8 W8A8 또는 48GB 2장 BF16 TP2**를 권한다. 80GB에서는 **BF16 54GB + KV 20GiB**로 무손실 정밀도를 산다. H100(3.35TB/s)과 A100(2.0TB/s)의 대역폭 차이 때문에 A100의 단일 스트림은 표보다 **30~40% 낮게** 잡아야 한다. 80GB의 대안인 35B-A3B FP8(42GB)은 최대 262K 단일 요청과 32K 기준 8~12슬롯(추)을 주지만, RAG에서 중요한 지시 준수(IFEval 95.0·IFBench 76.5)는 27B가 더 높다 ([Qwen3.5 모델카드](https://huggingface.co/Qwen/Qwen3.5-122B-A10B)). 장문 문서 요약이 주 업무라면 Gemma 4 31B가 128K MRCR 66.4로 서브-35B 최고 리콜을 보이므로, 27B의 대안이 아니라 **업무별 2모델 운영**이 낫다 ([Gemma 4 카드](https://ai.google.dev/gemma/docs/core/model_card_4)). 그래서 **80GB의 기본은 27B BF16, 동시성·장문이 계약 요건일 때만 35B FP8 또는 Gemma 4 31B**다. 이 구간은 저장소가 32K·TP1·vLLM 0.32.0으로 이미 검증한 구성과 정확히 일치한다.

141GB는 "애매한 구간"이 아니라 **122B를 INT4로 사는 구간**이다. 74GB 가중치 뒤에 55GiB KV가 남아 32K에서 12~25슬롯이 되고, 한국어 품질 손실도 AWQ-4bit KMMLU 71.20(원본 71.72, −0.7%)로 작다 ([axteam AWQ 카드](https://huggingface.co/axteam/Qwen3.5-122B-A10B-AWQ-4bit)). 대안인 35B-A3B BF16(84GB)은 KV 40GiB로 262K 장문에 유리하지만, 사내 규정 전반 QA처럼 **지식 커버리지**가 품질을 지배하는 업무에서는 122B INT4가 낫다. 반면 122B FP8은 계획치 147GB가 141GB를 넘어 **한 장 구성에서 제외**한다. 단, 저장소가 검증한 122B는 BF16·8K이므로 **INT4 단일 H200은 32K 기동·KV 로그를 먼저 확인**해야 한다. 이 한 줄이 141GB 티어의 존재 이유다.

288GB B300은 **122B FP8이 스위트스팟**이다. 147GB 가중치 뒤에 110GiB 이상의 KV가 남아 최대 262K 단일 요청과 32K 기준 20~40슬롯을 함께 지원하고, FP8은 한국어 품질 손실이 사실상 없다. 다만 저장소가 검증한 것은 BF16(268GB, 8K)이므로, **FP8 단일 B300은 스모크 테스트 후 채택**해야 한다. 품질 상한을 원하면 397B-A17B NVFP4(238GB)가 들어가지만 KV가 30~45GiB로 줄어 32K에서 2~3명이 한계다. "1장 최고 지능"과 "1장 최고 동시성"은 다른 답이다.

4×80GB와 8×80GB는 모델 선택보다 **배치 전략**이 중요하다. 4×80GB는 122B BF16(293GB)도 올릴 수 있는 최소 구성이지만 KV가 25GB로 줄어 32K에서 3~5슬롯이므로, 운영 기본은 FP8 TP4(147GB, KV 170GB)다. vLLM 권장은 처리량 중심에서 `-dp 4 --enable-expert-parallel --language-model-only`다 ([vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.5-122B-A10B)). 저장소는 TP4를 검증하지 않았으므로 기동 시 `GPU KV cache size`를 직접 읽어 슬롯을 확정해야 한다. 8×80GB에서 397B FP8(477GB)은 8×H200 SGLang에서 **동시 50·1,345 tok/s·TTFT ≤2초**가 측정됐고 ([SGLang #22603](https://github.com/sgl-project/sglang/discussions/22603)), 참고로 8×B200(1.4TB)에서는 **동시 32·1,269 tok/s·TTFT 1.94초**가 측정됐다 ([Lambda](https://lambda.ai/inference-models/Qwen/Qwen3.5-397B-A17B)). 그러나 한국어 사내 문서 QA에서 122B 대비 체감 격차는 검증된 바 없고, 397B는 KV 152GiB로 32K 50~80슬롯이 한계다. **일반 QA는 122B FP8 복제 2개, 계약서·코드·최고난도 추론만 397B**가 TCO와 장애 격리 모두에서 낫다. 복제 운영은 vLLM 인스턴스 2개 + LB 구성이며, 저장소는 단일 인스턴스만 검증했다.

CPU 구간은 "모델"이 아니라 "업무"를 바꿔야 한다. 35B-A3B INT4(21GB)는 EPYC 9554에서 30B급 MoE Q4 **~63 tok/s**가 측정됐지만 ([RunAIHome](https://runaihome.com/blog/best-cpu-for-llm-inference-2026)), 이는 짧은 프롬프트의 디코드 값이고 4~8K RAG 프롬프트에서는 prefill이 TTFT를 수 초로 밀어올린다. CPU 티어의 정답은 **배치 요약·분류·임베딩 보조**이며, 대화형 RAG는 24GB GPU를 최소선으로 잡아야 한다. 9B Q8을 대화형에, 35B-A3B INT4를 야간 배치에 두는 이중 구성이 현실적이다.

정리하면 Baryon AX의 **제품 카탈로그는 Qwen3.5-9B / 27B / 122B-A10B / 397B-A17B 4종에 장문 특화 Gemma 4 31B를 더한 5종**으로 고정한다. EXAONE 4.5는 한국어 문서 이해의 상한을 재는 벤치마크 참조 모델, Solar Open 2는 라이선스 확인 후의 한국어 특화 옵션, A.X K2는 8×141GB급 멀티노드가 있는 고객의 품질 상한, Kanana-2는 한국어 단일 목적 PoC로만 둔다. 모델 수를 늘리는 것보다 **같은 5종을 모든 티어에서 정밀도만 바꿔 쓰는 것**이 운영·평가·라이선스 비용을 줄인다.

### 품질-속도 6규칙

| 규칙 | 판정 기준 | 근거 |
|---|---|---|
| **1. 단일 스트림 25~30 tok/s 미만이면 품질 이득은 체감되지 않는다** | 인터랙티브 챗의 하한선. 이 아래면 모델을 키우지 말고 정밀도·컨텍스트를 줄인다 | 4-bit 70B가 4×3090에서 18 tok/s로 "대화는 되지만 에이전트 루프는 실패" ([cap10bad](https://github.com/cap10bad/llm-inference-benchmarks)) |
| **2. FP8이 INT4보다 먼저다** | 48GB 이상에서 INT4는 "안 들어갈 때"만. 한국어·코드·장문에서 INT4 손실이 커진다 | FP8은 99.3~100% 회복, W4A16은 코드/수학 −6~12% ([arXiv 2411.02355](https://arxiv.org/html/2411.02355v4)) |
| **3. 32K를 넘기면 대형 INT4보다 중형 FP8/BF16** | KV가 동시성을 지배한다. 80GB 한 장에 122B INT4를 넣는 구성은 KV 6GB로 32K 1~2명분이다 | KV = `2×L×H_kv×d_head×bytes`; 70B GQA 320KiB/token ([vLLM docs](https://docs.vllm.ai/en/latest/configuration/optimization.html)) |
| **4. MoE는 메모리 구매이지 지연 구매가 아니다** | 122B-A10B는 10B 활성으로 40~70 tok/s를 내지만 FP8 122GB·BF16 244GB를 상주시켜야 한다 | 8×H100 122B **1,585 tok/s @32** ([Lambda](https://lambda.ai/inference-models/qwen/qwen3.5-122b-a10b)) |
| **5. `max-model-len`은 P99에 맞춘다** | RAG는 8~16K가 실제 예산. 262K를 열어두면 KV만 태운다 | 128K에서 대부분 모델이 32K보다 성능 하락 ([LaRA](https://doi.org/10.48550/arxiv.2502.09977)) |
| **6. thinking은 기본 OFF, 출력은 상한을 건다** | Qwen3.5는 thinking이 기본 ON. RAG에서 근거 인용에 도움이 되는지 A/B 후 결정 | 모델카드 명시, 저장소 `MODEL_DISABLE_THINKING=true` ([b300-vllm.md](../docs/b300-vllm.md)) |

## 노트가 갈린 지점: 24GB·141GB·8×80GB, 그리고 숫자의 단위

두 연구 노트는 프레임이 다르다. `tier_recommendations`는 **vLLM 공식 최소 VRAM**을 기준으로 "들어가는 가장 큰 모델"을 골랐고, `model_quality`는 **실서비스 가능성**을 기준으로 "KV와 한국어 품질을 감당하는 모델"을 골랐다. 그래서 24GB·141GB·288GB·8×80GB에서 1순위가 뒤집힌다. 아래는 어느 쪽을 택했고 왜인지다.

| 쟁점 | A 노트 (`tier_recommendations`) | B 노트 (`model_quality`/`gpu_vram_fit`) | 이 보고서의 판정 |
|---|---|---|---|
| 24GB 1순위 | 35B-A3B INT4 (공식 최소 21GB) | 9B (27B 4bit는 "데모 가능, 서비스 불가") | **9B FP8 기본.** 35B INT4는 8K·1~2명 품질 우선 예외. KV 3GB가 결정적 |
| 48GB | 27B FP8 | "48~80GB 클래스"로 묶어 27B BF16 | **27B FP8.** BF16 54GB는 48GB에 물리적으로 안 들어감 |
| 80GB | 35B-A3B FP8 (262K) | 27B BF16 (지시 준수 최고) | **27B BF16 기본**, 35B FP8은 동시성·장문용 대안 |
| 141GB | 122B-A10B INT4 | 35B-A3B BF16 @128~262K | **122B INT4 기본**, 35B BF16은 장문·동시성 대안. FP8 불가는 양쪽 일치 |
| 288GB | 122B FP8 | 122B BF16 (저장소 검증) | **FP8 목표, BF16 폴백.** FP8 단일 B300은 미검증 |
| 8×80GB | 397B FP8 | 122B TP4 / 675B / V4 Flash | **122B FP8×2 기본, 397B는 품질 상한.** 일반 QA에서 397B 이득 미검증 |
| 4×80GB 집계 처리량 | 4,000~8,000 tok/s | 8×H100 122B **1,585 tok/s @32** | **800~1,500이 현실적.** 4,000+는 피크·다른 워크로드로 보임 |
| B200/B300 VRAM | 192GB / 288GB | 180 / 268 / 270 / 275GB | **단위 문제.** 288GB≈268.6GiB=275,040MiB. HGX 270GB는 예약분. 계획은 268GiB로 |
| 27B 모델 이름 | Qwen3.8-27B (2026-08) | Qwen3.5-27B (2026-02) | **둘 다 존재.** 저장소 검증은 3.5, 3.8은 hybrid KV로 업그레이드 후보 |
| 35B 이름 | 35B-A3B | HF 메타데이터 36B total | **36B total / 35B 마케팅.** 같은 모델 |
| Kanana-2 | 30B-A3B (HAE-RAE 88.7) | 3B급 (KMMLU 43.3) | **패밀리 내 다른 크기.** 혼용 금지 |
| DeepSeek V4 | — | V4 Flash 0731 (166.9GB) | **V4.1 Flash 1017(NVFP4 429GB)은 1차 자료 없음.** 견적 제외 |
| EXAONE 라이선스 | "상업 라이선스로 검토" | NC, **출력물까지 금지** | **NC가 맞다.** 별도 계약 없으면 제품 불가 |
| KMMLU | Qwen3 32B 61.4 / EXAONE 67.7 | Qwen3.5-122B **71.72 (KMMLU)** | **KMMLU와 KMMLU-Pro는 다른 시험.** 순위 비교 불가 |
| AA 지능지수 | — | 9B=32, 27B=20.1(Xhigh) | **스케일·시점 불일치.** 9B가 27B보다 높다는 결론은 오류 |
| gpu-memory-utilization | 기본 1.0 (extensible KV) | V1 기본 0.90 | **버전 차이.** 고정한 vLLM의 기동 로그로 확인 |

가장 큰 충돌은 **4×80GB 처리량**이다. A 노트의 4,000~8,000 tok/s는 8×A100/8×H100 측정에서 선형 스케일링한 값인데, 정작 인용한 Lambda 측정은 8×H100에서 122B·8K in/1K out·동시 32에 **1,585 tok/s**다. 4장이면 절반인 **800~1,000**이 맞고, 4,000은 짧은 프롬프트·높은 동시성·peak 조건에서나 나오는 값이다. 이 보고서는 **RAG 현실 워크로드 기준 800~1,500**을 채택한다. 8×80GB의 1,300~1,800은 SGLang 실측 1,345와 정합하므로 그대로 쓴다.

**벤치마크 스케일 충돌**은 더 위험하다. Qwen3.5-122B의 KMMLU 71.72와 A.X K2의 KMMLU-Pro 80.5는 난이도가 다른 시험이고, Solar Open 2의 78.4도 KMMLU-Pro다 ([A.X K2](https://arxiv.org/html/2608.30181v1), [Solar Open 2](https://arxiv.org/abs/2607.20062)). BCCard의 KMMLU 76.0은 **45과목 중 5과목·200샘플** 부분집합이라 전체 점수와 비교할 수 없다 ([BCCard 카드](https://huggingface.co/BCCard/MoAI-gemma-4-31B-it-mxfp4)). Artificial Analysis 지수도 v5.0.1에서 Qwen3.8-27B Xhigh가 20.1인데, 다른 글은 Qwen3.5-9B를 32로 적는다 ([gpuwerk](https://gpuwerk.com/blog/open-weight-model-leaderboard), [AA 소형 모델](https://artificialanalysis.ai/articles/qwen3-5-small-models)). 두 숫자가 같은 자로 잰 값이 아니므로 "9B가 27B보다 똑똑하다"는 결론은 성립하지 않는다. 이 보고서는 **같은 하네스의 Qwen3.5 패밀리 표**(27B MMLU-Pro 86.1·IFEval 95.0, 122B 86.7·93.4, 35B 85.3·91.9)와 **Gemma 4 자체 표**만 브랜드 내 비교에 쓰고, 브랜드 간 비교는 "방향"으로만 읽는다 ([Qwen3.5 카드](https://huggingface.co/Qwen/Qwen3.5-122B-A10B), [Gemma 4 카드](https://ai.google.dev/gemma/docs/core/model_card_4)).

**모델 이름 충돌**도 견적 사고를 만든다. Qwen3.5(2026-02)와 Qwen3.8(2026-08)은 **둘 다 27B dense를 갖는다**. 저장소가 검증한 것은 Qwen3.5-27B이고, Qwen3.8-27B는 48층 중 48층이 linear attention인 hybrid 구조라 1M 컨텍스트에서 KV가 6.6M 토큰까지 들어간다 ([Qwen3.8-27B recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)). 장문 RAG에서 3.8-27B의 KV 효율은 매력적이지만, 공개 검증은 32GB 5090 레시피뿐이므로 **업그레이드 후보**로만 둔다. Qwen3.6은 어떤 1차 자료에서도 확인되지 않아 표에서 제외했다. 35B-A3B는 HF 메타데이터상 36B total이고, Kanana-2는 30B-A3B와 3B급이 별도로 존재하며, DeepSeek는 V4 Flash 0731(166.9GB)과 "V4.1 Flash 1017"(NVFP4 429GB)이 뒤섞여 있는데 후자는 1차 카드가 없다. **이름이 확인되지 않은 모델은 견적에 넣지 않는다.**

**라이선스 충돌**은 타협하지 않는다. A 노트는 "EXAONE을 상업 라이선스로 검토"라고 썼지만, B 노트가 인용한 라이선스 원문은 상업 사용과 **출력물**을 명시적으로 금지한다. 즉 EXAONE으로 만든 답변을 고객에게 납품하는 것 자체가 문제다. Qwen3 MoE(235B-A22B)의 Apache 여부도 출처가 갈리지만, **Qwen3.5의 122B·397B와 Qwen3.8-27B는 LICENSE 파일로 Apache 2.0이 확인**되므로 Baryon의 기본값에는 영향이 없다. 27B·35B는 패밀리 라이선스를 따르는 것으로 보되, 계약 전 LICENSE 파일을 한 번 더 확인한다. Kanana는 자사 서비스는 무료여도 SI 패키징·온프레미스 어플라이언스·API 재판매에 Kakao 서면 허가가 필요하고, Solar Open 2는 라이선스가 확인되지 않았다. 이 세 개는 "기술 검토 대상"이지 "제안서 기본값"이 아니다 ([EXAONE LICENSE](https://github.com/LG-AI-EXAONE/EXAONE-4.0/blob/main/LICENSE), [Kanana 라이선스 정리](https://hokai.io/hub/models/kanana-2-30b-a3b-instruct-2601)).

## 양자화는 FP8이 기본, 라이선스는 그 앞에서 탈락시킨다

정밀도 사다리는 위가 평평하고 아래가 절벽이다. FP8은 사실상 무손실(99.3~100%), NVFP4는 FP8의 99~99.5%, 잘 만든 4-bit weight-only는 학술 벤치에서 96~99%지만 **코드·수학·장문 검색·비영어**에서 손실이 커진다. BF16→FP8은 단일 스트림 1.5~1.6배·배치 1.2~1.4배·가중치 절반이고, BF16→W4A16은 단일 2.2~2.7배·배치 1.3~1.6배다. 반면 4-bit 미만은 기울기가 아니라 절벽이다. Q3_K_M은 Q4_K_M 대비 perplexity 손실이 약 3.7배, Q2_K는 20배다 ([llama.cpp 양자화 표](https://www.logeshwaran.org/2026/10/gguf-quantization-types-explained.html)).

| 전환 | 품질 비용 | 단일 스트림 | 배치 처리량 | 가중치 VRAM | KV 용량 |
|---|---|---|---|---|---|
| BF16 → FP8 (Hopper+) | 0~−1% | 1.5~1.6× | 1.2~1.4× | ÷2 | +10~15% |
| BF16 → INT4 W4A16 | 평균 −1~4%, 코드/수학 −6~12% | 2.2~2.7× | 1.3~1.6× | ÷3.5 | +40~60% |
| BF16 → NVFP4 (B200/B300) | −0.5~−2% | 2.5~4× | 1.5~2.5× | ÷4 | +50~70% |
| BF16 KV → FP8 KV | −0.2~−2% (모델 의존) | 7K 이하 중립 | 20K에서 +15% | — | ×2 |

**한국어에서는 4-bit의 가격이 영어보다 비싸다.** 자동 지표 1.7% 하락이 인간 평가에서는 16% 하락으로 나타났고, 중국어·일본어·한국어가 W4에서 특히 harmed라는 보고가 있다 ([EMNLP 2024](https://aclanthology.org/2024.findings-emnlp.935.pdf), [arXiv 2407.03211](https://arxiv.org/html/2407.03211v2)). 영어 전용 캘리브레이션으로 INT3를 적용하면 한국어 perplexity가 3.49배로 뛴다 ([arXiv 2608.11786](https://arxiv.org/html/2608.11786v1)). 반대로 한국어 포함 캘리브레이션과 한국어 뉴런 보존을 적용하면 31B에서 **98.7% 회복**(BCCard Gemma 4 MXFP4), 3B에서 **+17.9%**(LAPE-AWQ)가 가능하다 ([BCCard 카드](https://huggingface.co/BCCard/MoAI-gemma-4-31B-it-mxfp4), [LAPE](https://koreascience.kr/article/JAKO202530839648324.page)). 규칙은 명확하다: **7B 미만은 4-bit 금지, 한국어 서비스의 4-bit는 한국어 캘리브레이션 + KoBEST/KMMLU/CLIcK 회귀 + 원어민 스팟체크 3종을 통과할 때만 허용.** Qwen3.5-122B의 AWQ-4bit가 KMMLU 99.3%를 유지했다는 벤더 측정은 긍정적이지만, 이는 객관식 지식 점수이고 RAG 충실도·장문 인용은 측정되지 않았다 ([axteam](https://huggingface.co/axteam/Qwen3.5-122B-A10B-AWQ-4bit)).

**KV 정밀도는 별개의 결정이다.** FP8 KV는 128K MRCR에서 97~98%를 회복하고, 약 7K 토큰을 넘으면 BF16 KV보다 빨라지며, 20K 부하에서 처리량이 15% 늘어난다. 다만 초기 Hopper FP8 FlashAttention-3 커널이 128K needle을 91%→13%로 떨어뜨린 사례가 있어 **엔진 버전을 고정하고 장문 평가를 업그레이드마다 재실행**해야 한다 ([vLLM FP8 KV blog](https://vllm.ai/blog/2026-04-22-fp8-kvcache)). NVFP4 KV는 Qwen3.5-397B에서 GSM8K 1문항(≈0.08점) 손실로 거의 무손실이지만, Qwen3.8-27B에서는 GPQA −1.01·SWE-bench −1.60으로 조금 더 깎인다 ([LMSYS](https://www.lmsys.org/blog/2026-09-16-nvfp4-kv-cache)). 4-bit KV는 쓰지 않는다.

**하드웨어별 정밀도 함정**은 두 개다. A100은 FP8 실리콘이 없어 FP8 체크포인트가 조용히 W8A16으로 로드된다. 디코드는 +48% 빨라지지만 **prefill TTFT는 1K에서 −19%, 16K에서 −28%** 나빠져, 장문 입력 RAG에서는 BF16이 8% 더 빠르다 ([A100 포팅 리포트](https://github.com/liuzl/qwen38-dgx-spark-lab/blob/main/docs/a100-porting-report.md)). A100 80GB의 RAG 기본은 **INT8 W8A8 또는 BF16**이고, FP8은 짧은 대화형에만 쓴다. B300은 반대로 INT8이 72 POPS에서 3 POPS로 붕괴해 **FP8/NVFP4만** 답이다 ([EMARQUE](https://emarque.ai/compare/h200-vs-b200-vs-b300)). MoE는 균일 4-bit가 위험하다. 라우터가 틀리면 모든 전문가 계산이 잘못된 토큰에 적용되므로 **라우터·어텐션은 8-bit, 전문가 FFN만 4-bit**이 원칙이고, A.X K2는 전문가 레이어에만 NVFP4를 적용해 FP8 평균의 99.0%를 유지했다 ([A.X K2 기술보고서](https://arxiv.org/html/2608.30181v1)).

### 라이선스: 성능표 1등이 계약서에서 탈락한다

| 모델군 | 라이선스 | 상업 온프레 | 함정 |
|---|---|---|---|
| Qwen3.5 27B/35B/122B/397B | Apache 2.0 | ✅ | 122B/397B는 LICENSE 1차 확인. 27B/35B는 패밀리 기준(계약 전 재확인) |
| Qwen3.8-27B | Apache 2.0 | ✅ | 2.4T-A95B는 "Qwen3.8-Max License"로 별도 ([DataNorth](https://datanorth.ai/news/alibaba-releases-qwen3-8-27b)) |
| Qwen3 MoE 235B-A22B | Apache vs 자체 약관 혼재 | ⚠️ | 1차 자료 미확정. 신규 도입 보류 |
| Gemma 4 | Apache 2.0 | ✅ | Gemma 3 약관에서 전환된 2026년 최대 호재 |
| **EXAONE 4.0/4.5** | **EXAONE AI Model License 1.2** | ❌ | **상업 사용·출력물 모두 금지.** 별도 계약 필수 |
| K-EXAONE 2.0 | EXAONE 계열 | ⚠️ | 가중치 공개 여부·조건 미확인 |
| Kanana-2 | 커스텀 | ⚠️ | SI 패키징·재판매·어플라이언스에 서면 허가, MAU 1,000만 |
| Solar Open 2 | 미확인 | ⚠️ | 견적 전 HF 원문 확인. 한국어 품질은 최상위 |
| A.X K2 (692B) | Apache 2.0 | ✅ | KMMLU-Pro 80.5·CLIcK 91.6의 한국어 상한. 단 8×141GB급 메모리 |
| DeepSeek V4 | MIT | ✅ | 재배포·에어갭 이미지에 가장 안전 |
| gpt-oss 20B/120B | Apache 2.0 | ✅ | 2025년 세대, 한국어 벤치 부재 |
| Llama 4 / (Llama 5?) | Llama Community | ⚠️ | MAU·명칭 조항, EU 멀티모달 예외. Llama 5 존재 자체가 미확정 |
| Mistral 3 / Large 3 | Apache 2.0 | ✅ | "Ecosystem license" 언급과 충돌, 원문 확인 |

Baryon AX의 납품 형태(SI 패키징·온프레미스 어플라이언스)를 기준으로 하면 **Qwen3.5/3.8, Gemma 4, DeepSeek V4, gpt-oss, A.X K2만 기본 후보**다. EXAONE 4.0 32B가 KMMLU-Pro 67.7로 동급 Qwen3 32B(61.4)를 앞서고, Solar Open 2가 KMMLU-Pro 78.4·CLIcK 90.7을 기록한 것은 사실이다 ([EXAONE 카드](https://huggingface.co/LGAI-EXAONE/EXAONE-4.0-32B), [Solar Open 2](https://arxiv.org/abs/2607.20062)). 그러나 EXAONE은 "한국어 품질 벤치마크의 상한"으로만 쓰고, 고객 요구가 있을 때 LG와 상업 계약을 별도 협의한다. Kanana와 Solar Open 2는 라이선스 원문을 확보하기 전까지 PoC 내부 평가용이다. AI 기본법이 2026-01-22 시행돼 고영향 AI·생성형 AI에 사전 고지·표시 의무가 있고 위반 시 3,000만원 이하 과태료가 부과되므로, **모델 버전·프롬프트·응답을 Langfuse로 남기는 것**이 라이선스 못지않은 아키텍처 요건이다 ([CLAP 정리](https://blog.clap.company/nexus/ai-basic-act/), [DFINITE](https://blog.dfinite.ai/ai-basic-law-business-guide)).

### vLLM: 기본값을 믿지 말고 기동 로그를 읽어라

RAG 처리량을 가장 크게 바꾸는 스위치는 모델 교체가 아니라 **prefix caching**이다. 4,000토큰 공통 시스템 프롬프트에서 L40S 한 장이 **1,902 tok/s(캐시 ON) vs 254 tok/s(OFF), 7.5배**였고, p99 TTFT는 0.384초 vs 23.9초였다 ([runcrate, 2026-10-27](https://dev.to/runcrate/vllm-prefix-caching-on-vs-off-75x-the-throughput-with-a-4000-token-system-prompt-on-one-l40s-31ol)). 반대로 캐시가 넘치면 hit rate가 0.83→0.00으로 떨어지며 p99 TTFT가 39초까지 치솟는다. 캐시는 켜되 **용량과 eviction을 계획**해야 한다.

| 함정 | 증상 | 대응 |
|---|---|---|
| `gpu-memory-utilization` 기본값 충돌 | 1.0(extensible KV)과 0.90(V1) 문서가 혼재 | 고정한 vLLM 버전의 기동 로그로 확인. 명시적 <1.0은 **하드 캡** ([vLLM docs](https://docs.vllm.ai/en/latest/configuration/optimization.html)) |
| 저장소의 246GB를 모델 크기로 오해 | "27B가 246GB를 쓴다"는 착각 | 27B BF16은 54GB. 246GB는 0.90 예약량. 공유 시 0.30 ([b300-vllm.md](../docs/b300-vllm.md)) |
| 32GB 카드의 27B NVFP4 | CUDA graph 캡처 OOM | `--enforce-eager`, `--max-num-seqs 8`, `--language-model-only` ([recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)) |
| native MXFP4 | `UnquantizedLinearMethod` 폴백으로 기동 실패 | compressed-tensors `mxfp4-pack-quantized` 사용 ([recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)) |
| 정적 YaRN 상시 활성 | 짧은 프롬프트에서도 품질·속도 손해 | 실제 장문 서비스일 때만 factor 적용 |
| Qwen3.5 thinking 기본 ON | 지연·토큰 폭증, RAG 형식 깨짐 | `chat_template_kwargs.enable_thinking=false`, `MODEL_DISABLE_THINKING=true` |
| 버전 하한 | 모델 로드 실패 | Qwen3.5는 vLLM ≥0.17/main, 저장소는 0.32.0. Blackwell은 cu130 nightly 시기 존재 |
| PCIe 카드 4-way TP | H100 대비 1/3 처리량 | 4장 이상은 NVLink 서버 또는 **복제+LB** ([CloudRift](https://www.cloudrift.ai/blog/benchmarking-rtx6000-vs-datacenter-gpus)) |
| `max-num-seqs` > KV | preemption(RECOMPUTE), TTFT 폭증 | `KV_tokens ÷ (prompt+output/2)`로 산정 ([vLLM docs](https://docs.vllm.ai/en/latest/configuration/optimization.html)) |
| prefix caching eviction | hit rate 0.83→0.00, p99 TTFT 39초 | 캐시 용량 계획, **cold/warm 분리 벤치** ([wojciech-wais](https://wojciech-wais.github.io/vllm-local-benchmarks)) |
| 8K `max-model-len` | Mem0 추출 프롬프트 8,193토큰이 400 오류 | 16K 이상 또는 메모리 추출 모델 분리 ([저장소 실측](../docs/b300-vllm.md)) |
| 122B FP8 147GB | H200 141GB에서 기동 OOM | INT4(74GB) 또는 2×H200/B300 ([recipe](https://recipes.vllm.ai/Qwen/Qwen3.5-122B-A10B)) |
| MoE hybrid CUDA graph | `num_cache_lines >= batch` assert | `--max-cudagraph-capture-size` 하향 ([recipe](https://recipes.vllm.ai/Qwen/Qwen3.5-122B-A10B)) |

운영 엔진은 **vLLM**, Ollama는 ①24GB PoC ②CPU/Apple 현장 ③Mem0 추출(qwen2.5:7b)에만 쓴다. Ollama는 과부하 시 느려지는 것이 아니라 **거부**하며, 100 동시 요청 실험에서 vLLM 15.20 req/s·오류 0% vs Ollama 0.45 req/s·**39.29% 실패**였다 ([Applied Sciences 16(11):5435, 2026](https://prodinit.com/blog/ollama-vs-vllm-self-hosted-llm-serving)). 데이터 서비스(Qdrant·PostgreSQL·Redis·Langfuse)는 GPU가 필요 없고, 임베딩/리랭킹은 FastEmbed(CPU)로 분리해 주력 모델의 KV를 보호한다.

## 도입 전 10문항: 우리 문서로 측정했는가

공개 벤치마크는 RAG 충실도를 예측하지 못한다. KMMLU 상위 모델도 K-FinHallu의 "정당한 거부" 축에서 무너지고 ([K-FinHallu 정리](https://benchmarklist.com/models/lg-ai-research-exaone-4.0-32b/)), 긴 컨텍스트가 RAG를 단조 개선하지도 않는다 ([arXiv 2411.03538](https://arxiv.org/html/2411.03538v1)). 검색 병목이 읽기 병목보다 크다는 2026년 연구도 같은 결론이다 ([arXiv 2610.26951](https://arxiv.org/html/2610.26951)). 그래서 마지막 관문은 모델표가 아니라 **고객 문서 300~500건의 골드셋**이다.

| # | 점검 항목 | 통과 기준 | 왜 |
|---|---|---|---|
| 1 | 한국어 골드셋 | HWP/HWPX·PDF 표·스캔 첨부·2~3문서 멀티홉 포함 300~500건 | 위키 기반 벤치와 사내 문서는 분포가 다르다 |
| 2 | 5축 분리 평가 | 정답성·인용 충실도·거부율·한국어 존댓말·p95 지연을 따로 채점 | 한 점수로 합치면 개선 방향을 잃는다 |
| 3 | 3모델 × 2정밀도 A/B | BF16/FP8 vs INT4, 최소 3개 모델 | 4-bit 델타는 하네스 노이즈 안에 있다 |
| 4 | thinking on/off | RAG 정답률과 지연을 동시에 비교 | 기본 ON이 RAG에 항상 유리하지 않다 |
| 5 | 파서 우선 | raw PDF vs OCR/표 추출 텍스트를 같은 모델로 비교 | 파서 델타가 모델 델타보다 클 수 있다 |
| 6 | 실제 컨텍스트 | 8K·32K에서 각각 측정, 262K는 별도 | 128K에서 대부분 모델이 32K보다 나빠진다 |
| 7 | 부하 테스트 | `vllm bench serve --random-input-len <실측> --max-concurrency <목표>` | 단일 스트림 수치로 용량을 계획하면 4~6배 과대추정 |
| 8 | KV 실측 | 기동 로그 `GPU KV cache size: N tokens` 확인, cold/warm 분리 | prefix cache가 A/B를 오염시킨다 |
| 9 | 라이선스 | 상업 사용·출력물·SI 패키징·MAU·재판매 5항목 서면 확인 | 성능 1등이 계약서에서 탈락한다 |
| 10 | 운영 | vLLM 버전 고정, readiness는 1토큰 워밍업 후, Langfuse 감사 | `/v1/models` 응답 ≠ 서빙 준비 완료 |

판정 규칙은 단순하게 둔다. **정답률 차이가 3점 미만이면 동급으로 보고 더 싼 쪽을 택한다.** 단일 스트림이 25~30 tok/s 아래로 떨어지면 품질이 높아도 인터랙티브 기본값으로 쓰지 않는다. 라이선스가 불확실한 모델은 PoC 점수와 무관하게 제안서에서 뺀다.

## 결론

이 조사의 가장 중요한 변화는 "더 큰 모델을 사려면 더 많은 GPU가 필요하다"가 아니라 **"KV 예산이 모델 선택을 역전시킨다"**는 사실이다. 141GB H200은 122B FP8이 약 6GB 모자라 INT4로 내려가야 하고, 24GB에서는 35B INT4보다 9B FP8이 더 많은 사용자를 감당한다. 반대로 288GB B300은 122B FP8로 262K와 20~40슬롯을 동시에 사는, 2026년의 유일한 "한 장 스위트스팟"이다. 8×80GB에서 397B를 올리는 것은 기술적으로 가능하지만, 한국어 사내 QA에서 그 지능 격차가 검증되지 않았고 KV는 오히려 122B 복제보다 불리하다. **품질 상한을 사는 것이 아니라, 계약상 최고난도 업무를 위한 별도 슬롯을 사는 것**으로 포지셔닝해야 한다. Qwen3.5를 고른 이유도 한국어 1등이라서가 아니라, 9B부터 397B까지 **한 라이선스·한 툴체인**으로 덮고 FP8/NVFP4 체크포인트가 vLLM에서 검증돼 있어 8개 티어를 같은 운영 절차로 돌릴 수 있기 때문이다.

두 번째 변화는 **라이선스가 하드웨어보다 앞선 필터**가 됐다는 점이다. EXAONE 4.5는 한국어 문서 이해에서 가장 좋은 기술적 선택이지만 출력물까지 상업 사용이 금지되어, Baryon AX의 제품 기본값이 될 수 없다. Kanana와 Solar Open 2도 원문 확인 전에는 견적에 넣을 수 없다. 결국 2026년의 권장안은 "티어마다 다른 벤더"가 아니라 **Qwen3.5 한 줄기 + Gemma 4 장문 특화 + 라이선스 확인된 한국어 모델의 예외**라는 단순한 구조가 된다. 이 구조의 경쟁력은 모델 교체가 아니라 **파싱·검색·평가·관측성**에서 나오며, 그것이 27B와 122B를 이미 검증한 저장소의 자산과 정확히 겹치는 지점이다.

## 출처

| 출처 | URL | 접속일 | 성격 |
|---|---|---|---|
| Qwen3.5-122B-A10B 모델카드 | https://huggingface.co/Qwen/Qwen3.5-122B-A10B | 2026-11-14 | 1차 |
| Qwen3.5-397B LICENSE | https://huggingface.co/Qwen/Qwen3.5-397B-A17B/blob/main/LICENSE | 2026-11-14 | 1차 |
| Qwen3.5-122B vLLM recipe | https://recipes.vllm.ai/Qwen/Qwen3.5-122B-A10B | 2026-11-14 | 1차 |
| Qwen3.5-35B-A3B recipe | https://recipes.vllm.ai/Qwen/Qwen3.5-35B-A3B | 2026-11-14 | 1차 |
| Qwen3.5-397B recipe | https://recipes.vllm.ai/Qwen/Qwen3.5-397B-A17B | 2026-11-14 | 1차 |
| Qwen3.8-27B recipe | https://recipes.vllm.ai/Qwen/Qwen3.8-27B | 2026-11-14 | 1차 |
| vLLM Qwen 레시피 전체 | https://recipes.vllm.ai/Qwen | 2026-11-14 | 1차 |
| Gemma 4 모델카드 | https://ai.google.dev/gemma/docs/core/model_card_4 | 2026-11-14 | 1차 |
| EXAONE 4.0 모델카드 | https://huggingface.co/LGAI-EXAONE/EXAONE-4.0-32B | 2026-11-14 | 1차 |
| EXAONE LICENSE | https://github.com/LG-AI-EXAONE/EXAONE-4.0/blob/main/LICENSE | 2026-11-14 | 1차 |
| EXAONE 4.5 기술보고서 | https://arxiv.org/html/2604.08644v1 | 2026-11-14 | 1차(벤더) |
| Kanana-2 GitHub | https://github.com/kakao/kanana-2 | 2026-11-14 | 1차 |
| Kanana 라이선스 정리 | https://hokai.io/hub/models/kanana-2-30b-a3b-instruct-2601 | 2026-11-14 | 2차 |
| Solar Open 2 기술보고서 | https://arxiv.org/abs/2607.20062 | 2026-11-14 | 1차(벤더) |
| A.X K2 기술보고서 | https://arxiv.org/html/2608.30181v1 | 2026-11-14 | 1차(벤더) |
| 397B 8×H200 SGLang 측정 | https://github.com/sgl-project/sglang/discussions/22603 | 2026-11-14 | 측정(커뮤니티) |
| 397B 8×B200 Lambda | https://lambda.ai/inference-models/Qwen/Qwen3.5-397B-A17B | 2026-11-14 | 측정(벤더) |
| 122B 8×H100 Lambda | https://lambda.ai/inference-models/qwen/qwen3.5-122b-a10b | 2026-11-14 | 측정(벤더) |
| 397B 8×RTX Pro 6000 Millstone | https://cdn.millstoneai.com/benchmarks/qwen3-5-397b-a17b-fp8-8x-rtx-pro-6000-blackwell/qwen3-5-397b-a17b-fp8-8x-rtx-pro-6000-blackwell.pdf | 2026-11-14 | 측정 |
| H100 27B vLLM/SGLang 비교 | https://winder.ai/vllm-vs-ollama-vs-sglang-llm-inference-comparison/ | 2026-11-14 | 측정 |
| H100 FP8 vs BF16 | https://dev.to/runcrate/vllm-fp8-vs-bf16-on-an-h100-14x-the-throughput-and-what-it-did-to-gsm8k-accuracy-2n86 | 2026-11-14 | 측정 |
| 24GB 9B 양자화 | https://computingforgeeks.com/gguf-vs-awq-vs-gptq/ | 2026-11-14 | 측정(블로그) |
| CPU MoE 63 tok/s | https://runaihome.com/blog/best-cpu-for-llm-inference-2026 | 2026-11-14 | 2차 |
| CPU prefill/TTFT | https://ardecode.github.io/llm/inference/benchmarking/2026/11/04/vllm-cpu-inference-granite-rapids/ | 2026-11-14 | 측정(블로그) |
| 4×3090 70B 18 tok/s | https://github.com/cap10bad/llm-inference-benchmarks | 2026-11-14 | 측정(공개 repo) |
| LaRA 장문 RAG | https://doi.org/10.48550/arxiv.2502.09977 | 2026-11-14 | 논문 |
| A100 FP8/W8A16 포팅 | https://github.com/liuzl/qwen38-dgx-spark-lab/blob/main/docs/a100-porting-report.md | 2026-11-14 | 측정 |
| vLLM 최적화 문서 | https://docs.vllm.ai/en/latest/configuration/optimization.html | 2026-11-14 | 1차 |
| vLLM FP8 KV | https://vllm.ai/blog/2026-04-22-fp8-kvcache | 2026-11-14 | 1차 |
| prefix caching 7.5× | https://dev.to/runcrate/vllm-prefix-caching-on-vs-off-75x-the-throughput-with-a-4000-token-system-prompt-on-one-l40s-31ol | 2026-11-14 | 측정 |
| FP8 품질(500k eval) | https://arxiv.org/html/2411.02355v4 | 2026-11-14 | 피어리뷰 |
| 한국어 W4 손실 | https://arxiv.org/html/2407.03211v2 | 2026-11-14 | 피어리뷰 |
| 한국어 INT3 PPL | https://arxiv.org/html/2608.11786v1 | 2026-11-14 | 논문 |
| 비영어 장문 양자화 | https://aclanthology.org/2025.emnlp-main.479.pdf | 2026-11-14 | 피어리뷰 |
| 비영어 인간평가 vs 자동지표 | https://aclanthology.org/2024.findings-emnlp.935.pdf | 2026-11-14 | 피어리뷰 |
| LAPE 한국어 뉴런 보존 | https://koreascience.kr/article/JAKO202530839648324.page | 2026-11-14 | 논문 |
| CloudRift PCIe vs NVLink | https://www.cloudrift.ai/blog/benchmarking-rtx6000-vs-datacenter-gpus | 2026-11-14 | 측정 |
| K-FinHallu | https://benchmarklist.com/models/lg-ai-research-exaone-4.0-32b/ | 2026-11-14 | 2차 |
| AA 리더보드 | https://artificialanalysis.ai/leaderboards/models | 2026-11-14 | 2차 |
| AA Qwen3.5 소형 모델 | https://artificialanalysis.ai/articles/qwen3-5-small-models | 2026-11-14 | 2차 |
| Qwen3.8-27B 라이선스/출시 | https://datanorth.ai/news/alibaba-releases-qwen3-8-27b | 2026-11-14 | 2차 |
| prefix cache 벤치 | https://wojciech-wais.github.io/vllm-local-benchmarks | 2026-11-14 | 측정(공개 repo) |
| NVFP4 KV | https://www.lmsys.org/blog/2026-09-16-nvfp4-kv-cache | 2026-11-14 | 측정 |
| BCCard Gemma 4 MXFP4 | https://huggingface.co/BCCard/MoAI-gemma-4-31B-it-mxfp4 | 2026-11-14 | 벤더 측정 |
| axteam Qwen3.5 AWQ | https://huggingface.co/axteam/Qwen3.5-122B-A10B-AWQ-4bit | 2026-11-14 | 벤더 측정 |
| B300 vs H200 | https://inferencex.semianalysis.com/compare/qwen-3-5-b300-vs-h200 | 2026-11-14 | 측정 |
| GPU 스펙 | https://inferencex.semianalysis.com/infx/gpu-specs | 2026-11-14 | 1차 |
| DGX B300 가이드 | https://docs.nvidia.com/dgx/dgxb300-user-guide/introduction-to-dgxb300.html | 2026-11-14 | 1차 |
| B300 전력/공급 | https://dash.packet.ai/blog/got-b300-why-early-supply-wins | 2026-11-14 | 2차 |
| 오픈웨이트 리더보드 | https://gpuwerk.com/blog/open-weight-model-leaderboard | 2026-11-14 | 2차 |
| 한국어 벤치 리더보드 | https://benchlm.ai/leaderboards/korean-benchmarks | 2026-11-14 | 2차 |
| KBS 한국어 벤치 | https://aclanthology.org/2026.aacl-long.198.pdf | 2026-11-14 | 피어리뷰 |
| RAG vs 장문 | https://arxiv.org/html/2411.03538v1 | 2026-11-14 | 논문 |
| 검색 병목 | https://arxiv.org/html/2610.26951 | 2026-11-14 | 논문 |
| Ollama vs vLLM | https://prodinit.com/blog/ollama-vs-vllm-self-hosted-llm-serving | 2026-11-14 | 2차(피어리뷰 인용) |
| AI 기본법 | https://blog.dfinite.ai/ai-basic-law-business-guide | 2026-11-14 | 2차 |
| 데이터 주권 | https://www.kspost.biz/ko-kr/articles/3131 | 2026-11-14 | 2차 |
| 저장소 B300 검증 | ../docs/b300-vllm.md | 2026-11-10 | 1차(실측) |
| 저장소 하드웨어 사양 | ../docs/hardware-requirements.md | 2026-11-14 | 1차 |
