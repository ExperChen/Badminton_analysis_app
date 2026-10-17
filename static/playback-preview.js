"use strict";

(() => {
  const panel = document.getElementById("playback-preview-panel");
  const button = document.getElementById("playback-preview-button");
  const status = document.getElementById("playback-preview-status");
  const player = document.getElementById("video");
  let matchId = null;
  let previewState = "idle";
  let poll = null;
  let requestId = 0;
  let activateWhenReady = false;

  function previewUrl(id) {
    return `/api/matches/${encodeURIComponent(id)}/playback-preview`;
  }

  function render(result) {
    previewState = result.state;
    button.disabled = result.state === "processing";
    const active = videoURL?.includes("/playback-preview/video");
    button.textContent = active ? "使用原片播放" : result.state === "ready" ? "切换到流畅播放" :
      result.state === "failed" ? "重新生成流畅版本" : "生成流畅播放版本";
    status.textContent = result.state === "processing" ? `正在生成 ${result.progress ?? 0}%` :
      result.state === "ready" ? "高清流畅 预览已就绪" :
      result.state === "failed" ? window.AppText.error(result.error || "生成失败") : "首次生成需要一些时间";
  }

  async function refresh(id = matchId) {
    if (!id) return;
    const token = ++requestId;
    try {
      const response = await fetch(previewUrl(id), {cache:"no-store"});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "无法读取预览状态");
      if (token !== requestId || id !== matchId) return;
      render(result);
      if (result.state === "ready" && activateWhenReady) {
        activateWhenReady = false;
        switchSource(true);
      }
      clearTimeout(poll);
      if (result.state === "processing") poll = setTimeout(() => refresh(id), 1200);
    } catch (error) {
      if (token === requestId && id === matchId) render({state:"failed", error:error.message});
    }
  }

  function switchSource(usePreview) {
    const id = matchId;
    if (!id || !data?.video || videoMatch !== id) return;
    const position = player.currentTime || 0;
    const resume = !player.paused;
    player.pause();
    videoURL = usePreview ? `${previewUrl(id)}/video` :
      `/api/matches/${encodeURIComponent(id)}/video?v=${encodeURIComponent(data.video.updated_at)}`;
    player.src = window.GGABPath(videoURL);
    player.addEventListener("loadedmetadata", () => {
      if (matchId !== id) return;
      player.currentTime = Math.min(position, Math.max(0, player.duration - .05));
      if (resume) player.play().catch(() => {});
    }, {once:true});
    player.load();
    render({state:previewState});
  }

  button.addEventListener("click", async () => {
    if (!matchId || !data?.video) return;
    if (videoURL?.includes("/playback-preview/video")) {
      switchSource(false);
      return;
    }
    if (previewState === "ready") {
      switchSource(true);
      return;
    }
    button.disabled = true;
    activateWhenReady = true;
    status.textContent = "正在启动预览生成…";
    try {
      const response = await fetch(previewUrl(matchId), {method:"POST", headers:{"X-GGAB-Local":"1"}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "无法启动预览生成");
      render(result);
      if (result.state === "ready") {
        activateWhenReady = false;
        switchSource(true);
      }
      if (result.state === "processing") poll = setTimeout(() => refresh(matchId), 1200);
    } catch (error) {activateWhenReady = false;render({state:"failed", error:error.message});}
  });

  document.addEventListener("ggab:loading", () => {
    clearTimeout(poll);
    requestId++;
    activateWhenReady = false;
    matchId = null;
    panel.hidden = true;
  });
  document.addEventListener("ggab:match-loaded", () => {
    matchId = data?.video && !pendingVideo ? data.match_id : null;
    panel.hidden = !matchId;
    if (matchId) refresh(matchId);
  });
  document.addEventListener("ggab:video-selected", () => {
    clearTimeout(poll);
    activateWhenReady = false;
    matchId = null;
    panel.hidden = true;
  });
})();
