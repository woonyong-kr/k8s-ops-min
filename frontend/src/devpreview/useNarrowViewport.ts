import { useEffect, useState } from "react";

// UI-PHASE2-METRICS-001 · priority 14 (responsive) · 공유 브레이크포인트 훅.
// 실뷰포트 폭(document.documentElement.clientWidth, px)이 maxPx 이하인지 관측한다.
// `.uni`의 zoom(PRESENT_SCALE)은 documentElement의 clientWidth에 영향을 주지 않으므로
// 여기서 읽는 값은 실제 CSS 뷰포트 폭이며 ROOT가 지정한 ≤768px 기준과 일치한다.
// window resize 이벤트로는 스크롤바 등장/소멸을 못 잡으므로 ResizeObserver도 함께 쓴다.
export function useNarrowViewport(maxPx = 768): boolean {
  const read = () => document.documentElement.clientWidth <= maxPx;
  const [narrow, setNarrow] = useState(read);
  useEffect(() => {
    const on = () => setNarrow(read());
    on();
    const ro = new ResizeObserver(on);
    ro.observe(document.documentElement);
    window.addEventListener("resize", on);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", on);
    };
    // read는 클로저로 안정적이며 maxPx 변경 시 재구독하도록 dep에 포함한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [maxPx]);
  return narrow;
}
