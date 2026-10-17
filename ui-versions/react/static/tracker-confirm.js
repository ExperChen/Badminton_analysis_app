(() => {
  function createConfirmation(request = (...args) => fetch(...args)) {
    let status = {connected: false, workflows: []};
    const pending = new Map(), acceptedCosts = new Map(), messages = new Map(), listeners = new Set();
    const emit = () => listeners.forEach(listener => listener());
    const flowFor = id => status.workflows?.find(flow => flow.project_id === id);
    const versionReady = (current, required) => {
      const parse = value => /^\d+\.\d+\.\d+$/.test(value || '') ? value.split('.').map(Number) : null;
      const installed = parse(current), minimum = parse(required);
      if (!installed || !minimum) return false;
      for (let i = 0; i < 3; i++) {
        if (installed[i] !== minimum[i]) return installed[i] > minimum[i];
      }
      return true;
    };
    function view(id) {
      const flow = flowFor(id), operation = pending.get(id);
      let reason = '';
      if (!flow || flow.stage !== 'await_confirmation') reason = '项目尚未处于费用确认阶段。';
      else if (operation) reason = '确认结果正在核对，请勿重复提交。';
      else if (!status.connected) reason = '分析服务暂时不可用，请联系管理员。';
      else if (flow.quota?.requires_assignment) reason = '请管理员先认领项目并分配 分析额度。';
      else if (flow.quota?.needs_recharge) reason = `分析额度不足，需要充值：预计 ${flow.estimated_cost?.toLocaleString()} 分析额度，可用 ${flow.quota.available_for_project?.toLocaleString()} 分析额度，请联系管理员。`;
      else if (['missing','failed','mismatch'].includes(status.video_checks?.[id]?.status))
        reason = '视频暂时不可用，请联系管理员核查。';
      else if (!versionReady(status.extension_version, flow.required_extension_version || '1.5.0'))
        reason = '分析服务需要更新，请联系管理员。';
      else if (!flow.confirmation_ready) reason = '正在核验服务报价，暂时无法提交。';
      else if (!flow.remote_project_id || !Number.isInteger(flow.estimated_cost) || flow.estimated_cost < 0)
        reason = '分析服务 尚未提供明确的项目和费用。';
      return {flow, operation, message: messages.get(id), reason, canConfirm: !reason};
    }
    function updateStatus(next) {
      status = next;
      for (const [id, operation] of pending) {
        const flow = flowFor(id), stage = flow?.stage;
        // A pending HTTP request owns its lock until its response is resolved.
        if (operation !== 'sending' && stage && stage !== 'await_confirmation') {
          pending.delete(id);
          acceptedCosts.delete(id);
          if (['confirm_tracking', 'processing', 'complete'].includes(stage)) messages.delete(id);
        } else if (operation === 'accepted' && stage === 'await_confirmation' &&
                   flow.estimated_cost !== acceptedCosts.get(id)) {
          pending.delete(id);
          acceptedCosts.delete(id);
          messages.set(id, '分析服务 已更正预计费用；未按旧金额提交。请核对新金额后再点击。');
        }
      }
      emit();
    }
    async function reconcile() {
      try {
        const response = await request('/api/bridge/status', {cache: 'no-store'});
        if (response.ok) updateStatus(await response.json());
      } catch { /* Keep the submission lock if the status read also fails. */ }
    }
    async function confirm(id, expected) {
      const current = view(id), flow = current.flow;
      if (!current.canConfirm) return false;
      if (flow.remote_project_id !== expected.remote_project_id || flow.estimated_cost !== expected.estimated_cost) {
        messages.set(id, '项目或费用已更新，请核对新金额后再次点击 确认提交。');
        emit();
        return false;
      }
      pending.set(id, 'sending'); messages.delete(id); emit();
      try {
        const response = await request('/api/bridge/confirm/' + encodeURIComponent(id), {
          method: 'POST', headers: {'Content-Type': 'application/json', 'X-GGAB-Local': '1'},
          body: JSON.stringify({remote_project_id: expected.remote_project_id, estimated_cost: expected.estimated_cost})
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
          // A 4xx is a rejected local request. A network/5xx outcome may have
          // committed; do not re-enable submission based on a stale status read.
          pending.set(id, 'unknown');
          if (response.status >= 400 && response.status < 500) pending.delete(id);
          messages.set(id, typeof result.detail === 'string' ? result.detail : '费用确认失败：HTTP ' + response.status);
        } else if (result.queued === true && result.remote_project_id === expected.remote_project_id &&
                   result.estimated_cost === expected.estimated_cost) {
          pending.set(id, 'accepted'); acceptedCosts.set(id, expected.estimated_cost);
          messages.set(id, '确认指令已登记，正在提交分析请求；处理状态和实际费用仍待服务确认。');
        } else {
          pending.set(id, 'unknown');
          messages.set(id, '确认响应无法核验，已停止重复提交；请等待状态同步。');
        }
      } catch {
        pending.set(id, 'unknown');
        messages.set(id, '连接中断，确认结果不确定；不会自动重试，请先核对状态。');
      }
      emit();
      await reconcile();
      return true;
    }
    async function confirmDisplayed(id, displayed) {
      const current = view(id);
      if (!current.canConfirm || !displayed ||
          current.flow.remote_project_id !== displayed.remote_project_id ||
          String(current.flow.estimated_cost) !== displayed.estimated_cost) return false;
      return confirm(id, {remote_project_id: displayed.remote_project_id,
        estimated_cost: current.flow.estimated_cost});
    }
    return {view, confirm, confirmDisplayed, updateStatus, status: () => status,
      subscribe(listener) {listeners.add(listener); return () => listeners.delete(listener);}};
  }

  const controller = createConfirmation();
  function render(target, id, title) {
    const state = controller.view(id), flow = state.flow || {};
    const node = (tag, className, text) => {
      const item = document.createElement(tag); item.className = className;
      if (text !== undefined) item.textContent = text;
      return item;
    };
    const unknown = state.operation === 'unknown';
    const submitting = !unknown && (Boolean(state.operation) || flow.stage === 'confirm_tracking');
    const stopped = ['uncertain', 'blocked'].includes(flow.stage);
    const card = node('section', 'tag-confirm-card' + (stopped ? ' is-failed' : ''));
    card.setAttribute('aria-label', '本地自动跟踪费用确认');
    const head = node('div', 'tag-confirm-head'), heading = node('div', '');
    heading.append(node('span', 'tag-status', stopped || unknown ? '等待核查' :
      submitting ? '正在提交' : '等待确认'), node('h3', '', '确认自动分析？'));
    const actions = node('div', 'tag-confirm-actions');
    const button = node('button', 'tag-confirm', unknown ? '核对确认结果…' : submitting ? '正在提交…' : '确认提交');
    button.type = 'button'; button.disabled = !state.canConfirm;
    button.setAttribute('aria-label', '确认此项目的 分析额度 费用并提交自动跟踪');
    const expected = {remote_project_id: flow.remote_project_id, estimated_cost: flow.estimated_cost};
    button.onclick = () => controller.confirm(id, expected);
    actions.append(button); head.append(heading, actions);
    const cost = node('p', 'tag-cost', '服务报价： ');
    cost.append(node('strong', '', Number.isInteger(flow.estimated_cost) ?
      flow.estimated_cost.toLocaleString() + ' 分析额度' : '等待 分析服务 计算'));
    const available=flow.quota?.uses_local_quota?flow.quota.available:flow.balance_before;
    cost.append(node('span', '', (flow.quota?.uses_local_quota?' · 我的可用额度: ':' · 服务余额： ') + (Number.isInteger(available) ?
      available.toLocaleString() + ' 分析额度' : '尚未记录')));
    const message = node('p', 'local-action-message'); message.setAttribute('role', 'status');
    message.setAttribute('aria-live', 'polite');
    const corrected = Number.isInteger(flow.estimate_changed_from) && Number.isInteger(flow.estimated_cost) &&
      flow.estimate_changed_from !== flow.estimated_cost ?
      `分析服务 预计费用已由 ${flow.estimate_changed_from.toLocaleString()} 更新为 ${flow.estimated_cost.toLocaleString()} 分析额度；旧金额未提交，请核对后再点击。` : '';
    message.textContent = (window.AppText?.error||String)([state.message, corrected].filter(Boolean).join(' · ')) ||
      (flow.stage === 'confirm_tracking' ? '正在提交分析请求，成功后将更新处理状态。' : state.reason);
    message.hidden = !message.textContent;
    card.append(head, node('p', 'tag-project-title', title || flow.video_filename || id), cost,
      node('p', 'tag-note', '确认后预留预计费用，分析服务 结果完成并核验真实费用后才从本账号结算。后台提交前会再次校验费用和 分析服务 余额；未确认不会提交。'), message);
    target.replaceChildren(card);
  }
  if (typeof window !== 'undefined') window.GGABConfirmation = {...controller, render};
  if (typeof module !== 'undefined') module.exports = {createConfirmation};
})();
