# 미리보기 데모 서버 (Google Cloud)

회사 내부망과 분리된 Google Cloud VM 한 대에 Baryon AX를 띄워, 링크와 데모 로그인만으로 누구에게나 보여 주는 데모입니다.

| 항목 | 내용 |
| --- | --- |
| 서버 | Compute Engine `e2-standard-4` 1대, 서울(`asia-northeast3`), 고정 IP, 항상 켜짐 |
| 밖에서 보이는 것 | Caddy만(80/443): HTTPS(Let's Encrypt) + 데모 로그인 하나. API·웹·Qdrant는 VM 안 루프백 |
| 데이터 | 가상의 회사 '데모상사' 문서 4개([demo-docs/](demo-docs/))와 사용 안내서. 실제 고객·사내 자료 없음 |
| 모델 | 설정값 하나(`demo.sh model <url>`). 비어 있으면 화면 데모(demo-local 답변) |
| 접속 관리 | SSH는 IAP로만(공개 22번 없음). 도커 소켓은 컨테이너에 주지 않음 |

## 쓰는 법

```bash
gcloud auth login                                  # 사람이 직접
GCP_PROJECT=<project> deploy/gcp/demo.sh create     # VM·고정 IP·방화벽, 첫 부팅에 빌드(약 10분)
deploy/gcp/demo.sh status                           # 주소. 로그인은 local/gcp-demo.env (git 밖)
DEMO_MODEL_KEY=<key> deploy/gcp/demo.sh model https://<모델 주소>/v1 baryon-ax-qwen
deploy/gcp/demo.sh host demo.example.com            # DNS를 고정 IP로 돌린 뒤 실제 이름으로
deploy/gcp/demo.sh update [git-ref]                 # 새 버전 배포
deploy/gcp/demo.sh logs | ssh | destroy
```

처음 주소는 `<IP>.sslip.io`라 DNS 없이 바로 HTTPS가 됩니다.

## 주의

- 웹은 dev 인증 모드라 로그인한 데모 사용자는 화면에서 관리자 역할로 바꿀 수 있습니다. 그래서 **샘플 데이터만** 둡니다.
  실제 자료를 올려 쓰려면 OIDC(Keycloak) 모드로 바꾸세요(docs/auth-oidc.md).
- 모델 키를 쓰면 VM 메타데이터에 저장됩니다(프로젝트 열람 권한자가 볼 수 있음). 데모 전용 키를 쓰세요.
- 비용: e2-standard-4 상시 + 60GB 디스크 + 고정 IP로 대략 월 $120 안팎(서울 리전 기준, 모델 비용 별도).
