/* Export the data already received, with a persistent selection across updates. */
window.cpkExport = (() => {
  const records = new Map(), selected = new Set();
  let busy = false;
  const text = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  function update() {
    const all = document.getElementById("exportAll"), choice = document.getElementById("exportSelected");
    if (!all) return;
    all.disabled = busy || records.size === 0;
    choice.disabled = busy || selected.size === 0;
    all.textContent = `전체 저장 (${records.size})`;
    choice.textContent = `선택 저장 (${selected.size})`;
    const list = document.getElementById("exportChoices");
    const keys = JSON.stringify([...records.keys()]);
    if (list && list.dataset.keys !== keys) {
      list.dataset.keys = keys;
      list.innerHTML = [...records.keys()].map(query => `<label><input type="checkbox" data-export-query="${text(query)}"> ${text(query)}</label>`).join("") || '<span class="muted">아직 저장할 결과가 없습니다.</span>';
    }
    document.querySelectorAll("[data-export-query]").forEach(input => {input.checked = selected.has(input.dataset.exportQuery);});
  }
  function report(markets) {
    for (const market of markets || []) {
      const live = records.get(market.keyword);
      if (live?.live) {
        for (const field of ["opportunity", "rarity", "demand", "steadiness"]) live[field] = market[field];
        continue;
      }
      records.set(market.keyword, {...market, query: market.keyword, items: market.top || [],
        total_count: market.latest?.result_count, sections: {
          search: market.related_ok === 0 ? "load_error" : market.collected_at ? "ok" : "unknown",
          autocomplete: market.autocomplete_ok === 0 ? "load_error" : market.autocomplete_ok === 1 ? "ok" : "unknown",
          sourcing: market.dome_exists == null ? "unknown" : "ok"
        }});
    }
    update();
  }
  function job(job) {
    if (!job.query) return;
    const result = job.result || {};
    records.set(job.query, {...records.get(job.query), live: true, query: job.query,
      items: result.items || [], related: result.related_keywords || [], autocomplete: job.autocomplete?.items || [],
      total_count: result.total_count, sections: {...job.sections},
      dome_exists: job.sections?.sourcing === "ok" ? job.sourcing?.exists : null,
      dome_count: job.sections?.sourcing === "ok" ? job.sourcing?.count : null,
      collected_at: new Date().toISOString(), sample: false});
    update();
  }
  async function save(mode) {
    if (busy) return;
    const chosen = [...records.values()].filter(record => mode === "all" || selected.has(record.query));
    if (!chosen.length) return;
    busy = true; update();
    const status = document.getElementById("exportStatus");
    status.textContent = "엑셀 파일을 만들고 있습니다…";
    try {
      const response = await fetch("/api/export", {method: "POST", headers: {
        "Content-Type": "application/json", "X-App-Password": document.getElementById("pw")?.value || ""
      }, body: JSON.stringify({records: chosen})});
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.message || "엑셀 저장에 실패했습니다. 다시 시도해 주세요.");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `cpk-${mode === "all" ? "전체" : "선택"}-${new Date().toISOString().slice(0,10)}.xlsx`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      status.textContent = `${chosen.length}개 키워드의 엑셀 파일을 저장했습니다.`;
    } catch (error) {status.textContent = error.message;}
    finally {busy = false; update();}
  }
  function init() {
    document.getElementById("exportAll").addEventListener("click", () => save("all"));
    document.getElementById("exportSelected").addEventListener("click", () => save("selected"));
    document.getElementById("selectAllExports")?.addEventListener("click", () => {records.forEach((_, key) => selected.add(key)); update();});
    document.getElementById("clearExports")?.addEventListener("click", () => {selected.clear(); update();});
    document.addEventListener("change", event => {
      if (!event.target.matches("[data-export-query]")) return;
      const query = event.target.dataset.exportQuery;
      if (event.target.checked) selected.add(query); else selected.delete(query);
      update();
    });
    update();
  }
  return {init, report, job, update};
})();
