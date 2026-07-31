-- 같은 대상이 서로 다른 실행에서 다시 적재된 경우를 찾는다.
--
-- 처음에는 (cluster_id, source_id, resource_uid, observed_at) 로 묶고
-- COUNT(*) > 1 을 봤다. 그런데 그 네 컬럼은 uq_catalog_normalized_evidence 의
-- 유일 키다. 제약이 이미 막고 있는 것을 검사가 다시 세고 있었던 것이라,
-- 이 질의는 어떤 데이터에서도 0행이었다. 잡는 것 없이 시간만 썼다.
--
-- 유일 제약이 막지 못하는 중복은 observed_at 이 미세하게 다른 재관측이다.
-- 재시도나 backfill 이 원천 타임스탬프를 다시 계산하면 정확히 그 모양이 된다.
-- 그래서 시각을 날짜 단위로 뭉개고, 같은 날짜를 서로 다른 run 이 적재한 것만 남긴다.
-- 날짜로 뭉개지 않고 그냥 시각만 빼면, 매일 관측되는 정상 리소스가 전부 걸린다.
-- 오래 살아 있는 리소스는 실행 수만큼 행이 있는 것이 정상이기 때문이다.
--
-- 기간 조건은 여전히 HAVING 에 둔다. WHERE 에 두면 GROUP BY 이전에 행이 걸러져서
-- 원본은 5일 전이고 복제본만 어제 들어온 쌍이 "1건짜리 그룹"으로 보인다.
SELECT
    cluster_id,
    source_id,
    resource_uid,
    DATE_TRUNC('day', observed_at) AS observed_day,
    COUNT(*)                   AS observation_count,
    COUNT(DISTINCT run_id)     AS run_count,
    MIN(observed_at)           AS first_observed_at,
    MAX(observed_at)           AS last_observed_at,
    MAX(ingested_at)           AS last_ingested_at,
    ARRAY_AGG(DISTINCT run_id) AS from_runs
FROM catalog_normalized_evidence
GROUP BY cluster_id, source_id, resource_uid, DATE_TRUNC('day', observed_at)
HAVING COUNT(DISTINCT run_id) > 1
   AND MAX(ingested_at) >= (:logical_ts - INTERVAL '2 days')
ORDER BY observation_count DESC;
