-- 같은 대상을 같은 관측 시각으로 두 번 적재한 경우를 찾는다.
--
-- 기간 조건을 WHERE에 두면 안 된다. GROUP BY 이전에 행이 걸러져서,
-- 원본은 5일 전이고 복제본만 어제 들어온 쌍이 "1건짜리 그룹"으로 보인다.
-- 재시도와 backfill이 만드는 중복이 정확히 그 모양이라,
-- 이 검사가 잡아야 할 유일한 실제 사례를 못 잡게 된다.
--
-- 그룹을 먼저 만들고 HAVING에서 최근 유입 여부를 본다.
SELECT
    cluster_id,
    source_id,
    resource_uid,
    observed_at,
    COUNT(*)                   AS duplicate_count,
    MIN(ingested_at)           AS first_ingested_at,
    MAX(ingested_at)           AS last_ingested_at,
    ARRAY_AGG(DISTINCT run_id) AS from_runs
FROM catalog_normalized_evidence
GROUP BY cluster_id, source_id, resource_uid, observed_at
HAVING COUNT(*) > 1
   AND MAX(ingested_at) >= (:logical_ts - INTERVAL '2 days')
ORDER BY duplicate_count DESC;
