from __future__ import annotations

import csv
import json
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUT = ROOT / "screenshots"
WIDTH = 1600
HEIGHT = 900

BG = "#08111f"
PANEL = "#111d2e"
GRID = "#26364d"
TEXT = "#e8eef7"
MUTED = "#9fb0c6"
CYAN = "#4fd1c5"
ORANGE = "#f6ad55"
RED = "#fc8181"
GREEN = "#68d391"


def svg_text(x: float, y: float, value: str, size: int, color: str = TEXT, **attrs: object) -> str:
    attributes = " ".join(f'{key.replace("_", "-")}="{escape(str(val))}"' for key, val in attrs.items())
    return (
        f'<text x="{x}" y="{y}" font-family="Apple SD Gothic Neo, Pretendard, sans-serif" '
        f'font-size="{size}" fill="{color}" {attributes}>{escape(value)}</text>'
    )


def rect(x: float, y: float, width: float, height: float, fill: str, radius: int = 0) -> str:
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{fill}"/>'


def line(x1: float, y1: float, x2: float, y2: float, color: str, width: int = 1) -> str:
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"/>'


def base(title: str, kicker: str, source: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        rect(0, 0, WIDTH, HEIGHT, BG),
        svg_text(80, 70, kicker, 22, CYAN, font_weight="700", letter_spacing="1"),
        svg_text(80, 125, title, 42, TEXT, font_weight="800"),
        line(80, 155, 1520, 155, GRID, 2),
        svg_text(80, 858, source, 17, MUTED),
        svg_text(1520, 858, "KYRO · EVIDENCE", 17, MUTED, text_anchor="end", letter_spacing="1"),
    ]


def finish(parts: list[str], filename: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    parts.append("</svg>")
    (OUT / filename).write_text("\n".join(parts), encoding="utf-8")


def render_daily_transfer() -> None:
    with (RAW / "aws-regional-transfer-daily.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows = [row for row in rows if float(row["usage_gb"]) > 0]
    values = [float(row["usage_gb"]) for row in rows]
    total = sum(values)
    gross_cost = sum(float(row["gross_usage_cost_usd"]) for row in rows)
    recent = sum(float(row["usage_gb"]) for row in rows if row["date"] >= "2026-07-20")
    maximum = max(values)

    parts = base(
        "Regional Data Transfer는 마지막 7일에 75.1%가 몰렸습니다",
        "AWS COST EXPLORER · 2026-07-04—27",
        "Source: AWS Cost Explorer · RecordType=Usage · Estimated=true · snapshot 2026-08-01 KST",
    )
    cards = [
        ("청구 방향 합계", f"{total / 1000:.2f} TB", ORANGE),
        ("편도 상당량", f"{total / 2:,.0f} billed GB", CYAN),
        ("크레딧 전 비용", f"${gross_cost:.2f}", RED),
        ("07-20—26 비중", f"{recent / total * 100:.1f}%", GREEN),
    ]
    for index, (label, value, color) in enumerate(cards):
        x = 80 + index * 370
        parts += [
            rect(x, 185, 340, 115, PANEL, 18),
            svg_text(x + 24, 222, label, 19, MUTED),
            svg_text(x + 24, 270, value, 34, color, font_weight="800"),
        ]

    left, top, chart_w, chart_h = 105, 340, 1390, 410
    for tick in range(0, 5001, 1000):
        y = top + chart_h - (tick / 5000) * chart_h
        parts += [line(left, y, left + chart_w, y, GRID), svg_text(92, y + 6, f"{tick/1000:.0f}TB", 15, MUTED, text_anchor="end")]
    bar_w = 44
    gap = (chart_w - len(rows) * bar_w) / (len(rows) - 1)
    for index, row in enumerate(rows):
        value = float(row["usage_gb"])
        height = value / 5000 * chart_h
        x = left + index * (bar_w + gap)
        y = top + chart_h - height
        color = ORANGE if row["date"] >= "2026-07-20" else CYAN
        parts.append(rect(x, y, bar_w, height, color, 5))
        day = row["date"][-2:]
        if day in {"04", "10", "15", "20", "22", "26"}:
            parts.append(svg_text(x + bar_w / 2, 785, day, 15, MUTED, text_anchor="middle"))
        if value == maximum:
            parts += [
                svg_text(x + bar_w / 2, y - 34, "최고", 16, RED, text_anchor="middle", font_weight="700"),
                svg_text(x + bar_w / 2, y - 10, f"{value:,.0f}GB", 16, TEXT, text_anchor="middle", font_weight="700"),
            ]
    parts.append(svg_text(left, 815, "7월 (일)", 16, MUTED))
    finish(parts, "01-aws-regional-transfer.svg")


def render_nodegroups() -> None:
    with (RAW / "cloudwatch-nodegroup-network.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))[:7]

    parts = base(
        "관리면 왕복과 게임→인프라 전송은 따로 봐야 합니다",
        "CLOUDWATCH EC2 · NETWORK DIRECTION",
        "AWS CloudWatch (EC2) · NetworkIn/Out Sum · not Cross-AZ-only · 2026-07-31 KST",
    )
    parts += [
        rect(80, 180, 1440, 95, PANEL, 18),
        svg_text(110, 218, "해석", 19, MUTED),
        svg_text(110, 255, "32개 워커만의 비용이 아니라 management 내부 왕복 + game→infra 데이터면 전송이 함께 컸습니다.", 27, TEXT, font_weight="700"),
        rect(1210, 198, 18, 18, ORANGE, 4),
        svg_text(1238, 214, "NetworkOut", 17, MUTED),
        rect(1210, 232, 18, 18, CYAN, 4),
        svg_text(1238, 248, "NetworkIn", 17, MUTED),
    ]
    max_value = 5500
    scale = 810 / max_value
    y = 300
    for row in rows:
        out_value = float(row["network_out_gib"])
        in_value = float(row["network_in_gib"])
        name = row["logical_nodegroup"]
        parts += [
            svg_text(80, y + 22, name, 19, TEXT, font_weight="700"),
            rect(375, y, 810, 21, GRID, 7),
            rect(375, y, out_value * scale, 21, ORANGE, 7),
            svg_text(1205, y + 17, f"out {out_value / 1024:.2f}TiB", 16, ORANGE),
            rect(375, y + 29, 810, 21, GRID, 7),
            rect(375, y + 29, in_value * scale, 21, CYAN, 7),
            svg_text(1205, y + 46, f"in  {in_value / 1024:.2f}TiB", 16, CYAN),
        ]
        y += 66
    parts += [
        rect(80, 775, 1440, 50, "#17263a", 12),
        svg_text(100, 808, "주의: NetworkIn/Out에는 same-AZ·Cross-AZ·인터넷·제어면 트래픽이 함께 포함됩니다.", 18, MUTED),
    ]
    finish(parts, "02-cloudwatch-nodegroup-direction.svg")


def render_architecture() -> None:
    parts = base(
        "이벤트 경계는 유지하고, 배포 경계만 다시 정했습니다",
        "ARCHITECTURE DECISION · IMPLEMENTED, NOT YET REDEPLOYED",
        "Evidence: Git rev 2724af33d + python src/entrypoints/app.py --check · AWS cost reduction not measured yet",
    )
    parts += [
        svg_text(100, 210, "BEFORE · 2026-07-25 Git", 22, ORANGE, font_weight="800"),
        svg_text(865, 210, "AFTER · controller check", 22, CYAN, font_weight="800"),
        rect(80, 235, 680, 500, PANEL, 22),
        rect(840, 235, 680, 500, PANEL, 22),
    ]
    before_cards = [("47", "Deployment 문서"), ("42", "서비스 entrypoint"), ("32", "*-worker entrypoint"), ("56", "선언 replica 합계")]
    for index, (value, label) in enumerate(before_cards):
        x = 115 + (index % 2) * 300
        y = 285 + (index // 2) * 130
        parts += [
            rect(x, y, 265, 105, "#1a293d", 16),
            svg_text(x + 22, y + 46, value, 38, ORANGE, font_weight="800"),
            svg_text(x + 22, y + 80, label, 18, MUTED),
        ]
    parts += [
        svg_text(115, 575, "worker A Pod", 18, TEXT),
        svg_text(300, 575, "→ NATS →", 18, ORANGE, font_weight="700"),
        svg_text(415, 575, "worker B Pod", 18, TEXT),
        svg_text(600, 575, "→ …", 18, ORANGE, font_weight="700"),
        svg_text(115, 635, "논리 단계마다 serialize · broker · CNI · DB 경계를 통과", 19, MUTED),
        svg_text(115, 690, "장점: 독립 재시도·격리", 18, GREEN),
        svg_text(405, 690, "누락: 통신·운영 비용", 18, RED),
    ]
    parts += [
        rect(885, 285, 590, 235, "#132d32", 18),
        svg_text(925, 335, "1 controller process", 31, CYAN, font_weight="800"),
        svg_text(925, 385, "39 management services", 24, TEXT, font_weight="700"),
        svg_text(925, 430, "33 worker + 4 async + 2 HTTP", 20, MUTED),
        svg_text(925, 475, "InMemoryEventBus · same EventEnvelope", 20, MUTED),
        line(1180, 520, 1180, 565, CYAN, 3),
        rect(885, 565, 590, 105, "#1a293d", 18),
        svg_text(925, 610, "2 agent services remain separate", 24, TEXT, font_weight="700"),
        svg_text(925, 646, "target-cluster trust boundary", 19, MUTED),
        svg_text(885, 710, "설계 기준: 같은 소유·DB·릴리스·장애 영역이면 합치고, 권한 경계는 분리", 18, GREEN),
    ]
    finish(parts, "03-architecture-before-after.svg")


def render_benchmark() -> None:
    with (RAW / "event-bus-benchmark.json").open(encoding="utf-8") as handle:
        data = json.load(handle)
    local = data["inprocess"]
    nats = data["nats_jetstream_loopback"]
    comparison = data["comparison"]

    parts = base(
        "같은 프로세스의 이벤트 왕복은 373.955ms → 12.182ms였습니다",
        "LOCAL TRANSPORT MICROBENCHMARK · MEDIAN OF 5 ROUNDS",
        "Apple M4 · NATS 2.11 Docker loopback · transport only, not end-to-end or AWS cost reduction",
    )
    parts += [
        rect(80, 185, 700, 145, PANEL, 18),
        svg_text(115, 225, "batch 완료시간", 19, MUTED),
        svg_text(115, 285, f"-{comparison['batch_time_reduction_percent']:.2f}%", 46, GREEN, font_weight="800"),
        rect(820, 185, 700, 145, PANEL, 18),
        svg_text(855, 225, "전송 처리량", 19, MUTED),
        svg_text(855, 285, f"{comparison['throughput_multiplier']:.1f}×", 46, CYAN, font_weight="800"),
    ]
    chart_left, chart_top, max_width = 330, 410, 1040
    nats_width = max_width
    local_width = local["median_total_ms"] / nats["median_total_ms"] * max_width
    parts += [
        svg_text(80, chart_top + 32, "NATS JetStream", 22, TEXT, font_weight="700"),
        rect(chart_left, chart_top, max_width, 48, GRID, 10),
        rect(chart_left, chart_top, nats_width, 48, ORANGE, 10),
        svg_text(1395, chart_top + 33, f"{nats['median_total_ms']:.3f}ms", 21, ORANGE, font_weight="800"),
        svg_text(80, chart_top + 137, "In-process", 22, TEXT, font_weight="700"),
        rect(chart_left, chart_top + 105, max_width, 48, GRID, 10),
        rect(chart_left, chart_top + 105, local_width, 48, CYAN, 10),
        svg_text(1395, chart_top + 138, f"{local['median_total_ms']:.3f}ms", 21, CYAN, font_weight="800"),
        rect(80, 620, 1440, 125, "#122338", 18),
        svg_text(115, 660, "측정 계약", 19, MUTED),
        svg_text(115, 705, "EventEnvelope 1,393B × 1,000건/round × warm-up 1 + 측정 5", 24, TEXT, font_weight="700"),
        svg_text(1000, 660, "처리량", 19, MUTED),
        svg_text(1000, 705, f"{nats['events_per_second']:,.0f} → {local['events_per_second']:,.0f} events/s", 24, TEXT, font_weight="700"),
        rect(80, 780, 1440, 50, "#2a1f24", 12),
        svg_text(105, 813, "금지된 결론: 전체 시스템 82K events/s · AWS 비용 96.74% 절감", 19, RED, font_weight="700"),
    ]
    finish(parts, "04-event-bus-benchmark.svg")


def render_event_contract() -> None:
    parts = base(
        "처리량보다 먼저 중복 실행의 시간 계약을 고정했습니다",
        "EVENT CONTRACT · CODE DEFAULTS, NOT A LOAD TEST",
        "Code: contracts/event_bus/interfaces.py · events/bus.py · runtime/worker.py · runtime/relay.py",
    )
    parts += [
        svg_text(90, 205, "EVENT ENVELOPE", 21, CYAN, font_weight="800"),
        rect(80, 230, 690, 440, PANEL, 20),
    ]
    fields = [
        ("event_id", "consumer별 멱등 ledger key"),
        ("subject", "routing · 같은 subject 순서 보존"),
        ("correlation_id", "한 장애 흐름 전체 추적"),
        ("causation_id", "직전 원인 이벤트 추적"),
        ("schema_version", "호환 규칙"),
        ("workspace_id", "tenant · 권한 경계"),
    ]
    for index, (field, meaning) in enumerate(fields):
        y = 275 + index * 58
        parts += [
            svg_text(115, y, field, 21, ORANGE, font_weight="700"),
            svg_text(335, y, meaning, 20, TEXT),
        ]
    parts += [
        rect(110, 595, 610, 45, "#132d32", 10),
        svg_text(130, 625, "업무 쓰기 + Outbox + ledger 완료 = one DB transaction", 19, GREEN, font_weight="700"),
        svg_text(850, 205, "BACKPRESSURE & RETRY", 21, CYAN, font_weight="800"),
        rect(830, 230, 690, 440, PANEL, 20),
    ]
    limits = [
        ("30s < 60s < 90s", "handler < ACK wait < stale claim"),
        ("fetch 1", "같은 subject 순차 처리"),
        ("concurrency 8", "서로 다른 subject만 병렬"),
        ("max_ack_pending 100", "consumer in-flight 상한"),
        ("Outbox 10 / run", "순차 publish · 성공 건만 sent"),
        ("3 attempts → DLQ", "무한 재시도 방지"),
        ("512MiB or 7 days", "JetStream 보존 경계"),
    ]
    for index, (value, meaning) in enumerate(limits):
        column = index % 2
        row = index // 2
        x = 870 + column * 310
        y = 270 + row * 92
        parts += [
            rect(x, y, 280, 72, "#1a293d", 13),
            svg_text(x + 18, y + 29, value, 20, ORANGE if index == 0 else CYAN, font_weight="800"),
            svg_text(x + 18, y + 56, meaning, 16, MUTED),
        ]
    parts += [
        rect(80, 710, 1440, 110, "#2a1f24", 16),
        svg_text(115, 752, "운영에서 보존하지 못한 값", 19, RED, font_weight="800"),
        svg_text(115, 790, "event/s · payload p95/p99 · consumer lag p95 · AZ별 bytes", 23, TEXT, font_weight="700"),
        svg_text(1010, 790, "→ 운영 처리량 주장은 보류", 22, RED, font_weight="800"),
    ]
    finish(parts, "06-event-contract-and-limits.svg")


def render_airflow_failure() -> None:
    parts = base(
        "검증 스크립트가 Airflow의 archive mount를 끊었습니다",
        "FAILURE REPLAY · LOCAL AIRFLOW 2.10.5",
        "Evidence: airflow-bind-mount-failure.csv · airflow-validation.json · local Docker, not production",
    )
    parts += [
        svg_text(95, 205, "1 · FAILURE", 21, RED, font_weight="800"),
        svg_text(585, 205, "2 · ROOT CAUSE", 21, ORANGE, font_weight="800"),
        svg_text(1075, 205, "3 · FIX & PROOF", 21, GREEN, font_weight="800"),
        rect(80, 230, 450, 455, PANEL, 20),
        rect(565, 230, 450, 455, PANEL, 20),
        rect(1050, 230, 470, 455, PANEL, 20),
        svg_text(115, 290, "4 / 4 extract", 36, RED, font_weight="800"),
        svg_text(115, 330, "up_for_retry", 30, TEXT, font_weight="700"),
        rect(110, 370, 390, 125, "#2a1f24", 14),
        svg_text(135, 407, "FileNotFoundError", 21, RED, font_weight="800"),
        svg_text(135, 443, "/opt/project/.catalog-archive/", 17, TEXT),
        svg_text(135, 473, "2026-08-01", 17, TEXT),
        svg_text(115, 555, "fixture 실행은 성공했지만", 19, MUTED),
        svg_text(115, 590, "container 재실행에서만 터졌습니다.", 19, MUTED),
        svg_text(600, 285, "rm -rf mount root", 26, ORANGE, font_weight="800"),
        svg_text(600, 335, "↓", 30, ORANGE, font_weight="800"),
        svg_text(600, 380, "container는 삭제된 inode를 보존", 19, TEXT, font_weight="700"),
        svg_text(600, 430, "↓", 30, ORANGE, font_weight="800"),
        svg_text(600, 475, "호스트의 동일 경로 재생성은", 19, TEXT),
        svg_text(600, 510, "기존 bind mount를 복구하지 못함", 19, TEXT),
        rect(595, 565, 390, 80, "#2d271b", 14),
        svg_text(620, 600, "파일이 아니라", 18, MUTED),
        svg_text(620, 628, "mount 수명 계약의 문제", 21, ORANGE, font_weight="800"),
        svg_text(1085, 285, "root inode 유지", 26, GREEN, font_weight="800"),
        svg_text(1085, 323, "날짜별 child만 정리", 20, TEXT),
        rect(1080, 370, 405, 78, "#132d32", 14),
        svg_text(1105, 405, "7 / 7 task", 28, GREEN, font_weight="800"),
        svg_text(1290, 405, "SUCCESS", 22, TEXT, font_weight="700"),
        rect(1080, 470, 405, 78, "#132d32", 14),
        svg_text(1105, 505, "5 → 5 rows", 28, GREEN, font_weight="800"),
        svg_text(1290, 505, "same date", 18, MUTED),
        rect(1080, 570, 405, 78, "#132d32", 14),
        svg_text(1105, 605, "15 / 15", 28, GREEN, font_weight="800"),
        svg_text(1260, 605, "PostgreSQL checks", 18, MUTED),
        rect(80, 725, 1440, 90, "#17263a", 16),
        svg_text(110, 764, "검증의 완료 조건", 18, MUTED),
        svg_text(110, 799, "정상 1회가 아니라 · 실패 상태 보존 · 수정 후 재실행 · 동일 날짜 멱등성", 23, TEXT, font_weight="700"),
    ]
    finish(parts, "07-airflow-failure-to-proof.svg")


def main() -> None:
    render_daily_transfer()
    render_nodegroups()
    render_architecture()
    render_benchmark()
    render_event_contract()
    render_airflow_failure()
    print(f"rendered 6 SVG evidence boards in {OUT}")


if __name__ == "__main__":
    main()
