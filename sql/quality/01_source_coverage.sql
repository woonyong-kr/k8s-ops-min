-- 등록된 소스와 실제 실행을 대조한다.
--
-- 7일째 아무것도 만들지 못한 소스가 결과에서 사라지면 안 된다.
-- LEFT JOIN을 쓰고 기간 조건을 ON 절에 둔다. WHERE에 두면 실행 0건인 소스가
-- 조인 후 필터에서 탈락해, 침묵하는 소스와 존재하지 않는 소스가 같은 부재로 뭉개진다.
--
-- 성공 판정에 NO_DATA를 포함한다. NO_DATA는 "정상 실행, 신규 데이터 없음"이고
-- 04번 문서가 FAILED와 분리한 이유가 바로 그것이다. 실패로 세면 조용한 정상 소스가
-- NEVER_SUCCEEDED(error)로 찍힌다.
--
-- TRUNCATED도 실패가 아니다. 02번 문서에서 상한은 설계된 정상 동작이다.
-- 다만 상시화되면 범위 조정이 필요하므로 별도 지표로 센다.
--
-- last_run_date는 기간 밖 실행도 포함해야 한다. 기간 안에 실행이 없을 때
-- 마지막으로 언제 돌았는지가 분류에 필요한 유일한 정보다.
WITH windowed AS (
    SELECT
        s.source_id,
        s.name,
        s.enabled,
        COUNT(r.run_id) FILTER (
            WHERE r.logical_date >= (CAST(:logical_date AS date) - INTERVAL '7 days')
        )                                                                    AS runs_in_window,
        COUNT(*) FILTER (
            WHERE r.status IN ('SUCCESS','NO_DATA')
              AND r.logical_date >= (CAST(:logical_date AS date) - INTERVAL '7 days')
        )                                                                    AS healthy,
        COUNT(*) FILTER (
            WHERE r.status = 'TRUNCATED'
              AND r.logical_date >= (CAST(:logical_date AS date) - INTERVAL '7 days')
        )                                                                    AS truncated,
        COUNT(*) FILTER (
            WHERE r.status = 'FAILED'
              AND r.logical_date >= (CAST(:logical_date AS date) - INTERVAL '7 days')
        )                                                                    AS failed,
        MAX(r.logical_date)                                                  AS last_run_date
    FROM catalog_data_sources AS s
    LEFT JOIN catalog_collection_runs AS r ON r.source_id = s.source_id
    GROUP BY s.source_id, s.name, s.enabled
)
SELECT
    source_id, name, runs_in_window, healthy, truncated, failed, last_run_date,
    CASE
        WHEN enabled AND runs_in_window = 0                      THEN 'ENABLED_BUT_SILENT'
        WHEN NOT enabled AND runs_in_window > 0                  THEN 'DISABLED_BUT_RUNNING'
        WHEN runs_in_window > 0 AND healthy = 0                  THEN 'NEVER_HEALTHY'
        WHEN runs_in_window > 0
             AND healthy::numeric / runs_in_window < 0.8         THEN 'DEGRADED'
        WHEN runs_in_window > 0
             AND truncated::numeric / runs_in_window > 0.5       THEN 'CHRONICALLY_TRUNCATED'
    END AS finding
FROM windowed
WHERE (enabled AND runs_in_window = 0)
   OR (NOT enabled AND runs_in_window > 0)
   OR (runs_in_window > 0 AND healthy::numeric / runs_in_window < 0.8)
   OR (runs_in_window > 0 AND truncated::numeric / runs_in_window > 0.5);
