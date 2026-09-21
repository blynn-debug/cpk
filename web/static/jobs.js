/* Shared progressive job client. Status reads never start another search. */
window.cpkJobs = {
  labels: {pending: "조회 중", ok: "완료", no_results: "결과 없음", timeout: "시간 초과",
    challenge: "접근 제한", http_error: "조회 실패", load_error: "조회 실패", paused: "대기 중",
    budget: "오늘 한도 초과"},
  async search(query, {password = "", onUpdate, signal}) {
    const started = performance.now();
    const requestId = crypto.randomUUID();
    const headers = {"Content-Type": "application/json", "X-App-Password": password};
    const read = async (url, options = {}) => {
      const response = await fetch(url, {...options, headers, signal, cache: "no-store"});
      const data = await response.json();
      if (!response.ok || (data.error && !data.request_id)) {
        const messages = {auth: "비밀번호를 확인해 주세요.", busy: "다른 검색이 진행 중입니다. 잠시 후 다시 시도해 주세요.",
          worker_unavailable: "검색 서버에 연결하지 못했어요.", not_found: "검색 작업을 찾을 수 없어요.",
          worker_restarted: "검색 서버가 재시작되어 작업이 중단됐어요."};
        throw new Error(data.message || messages[data.error] || "검색 상태를 가져오지 못했어요.");
      }
      return data;
    };
    let job = await read("/api/jobs", {method: "POST", body: JSON.stringify({q: query, request_id: requestId})});
    let firstResult = null;
    let essentialReady = null;
    while (true) {
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      if (firstResult === null && [job.sections?.search, job.sections?.autocomplete].includes("ok")) {
        firstResult = performance.now() - started;
      }
      if (essentialReady === null && ["ok", "no_results"].includes(job.sections?.search) && job.sections?.autocomplete === "ok") {
        essentialReady = performance.now() - started;
      }
      window.cpkLastTiming = {request_id: job.request_id, first_result_ms: firstResult,
        essential_ready_ms: essentialReady, elapsed_ms: performance.now() - started, transport_ms: job.transport_ms};
      onUpdate(job);
      if (["complete", "partial", "failed"].includes(job.state)) {
        fetch("/api/jobs/" + encodeURIComponent(job.request_id) + "/timing", {
          method: "POST", headers, body: JSON.stringify(window.cpkLastTiming), keepalive: true
        }).catch(() => {});
        return job;
      }
      if (performance.now() - started > 100000) throw new Error("검색 상태 확인 시간이 초과됐어요.");
      await new Promise(resolve => setTimeout(resolve, 1000));
      job = await read("/api/jobs/" + encodeURIComponent(job.request_id));
    }
  }
};
