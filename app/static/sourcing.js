/* M8 多源合并页：载入样例、确认等价关系、发布现网、算多源影响面。只读渲染，动作后刷新。 */
(() => {
  function setMessage(text, kind) {
    const node = document.getElementById('sourcing-msg');
    if (!node) return;
    node.textContent = text;
    node.className = 'msg' + (kind ? ' msg-' + kind : '');
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    let body = null;
    try { body = await response.json(); } catch (err) { body = null; }
    return { ok: response.ok, status: response.status, body };
  }

  function errorText(body, status) {
    if (!body) return '失败 ' + status;
    return [body.error, body.message].filter(Boolean).join(' / ');
  }

  async function runAction(url, payload, okText) {
    setMessage('处理中…');
    const result = await postJson(url, payload);
    if (result.ok) {
      setMessage(okText, 'ok');
      window.setTimeout(() => window.location.reload(), 600);
    } else {
      setMessage(errorText(result.body, result.status), 'bad');
    }
  }

  function wireButtons() {
    document.querySelectorAll('[data-action]').forEach((button) => {
      button.addEventListener('click', () => {
        const action = button.dataset.action;
        if (action === 'bundle') {
          runAction('/api/sourcing/sample-bundle', {}, '样例来源已载入');
        } else if (action === 'publish') {
          runAction('/api/sourcing/revisions/publish-latest', {}, '现网已全量切换');
        } else if (action === 'confirm') {
          runAction('/api/sourcing/links/' + button.dataset.link + '/confirm',
                    {}, '等价关系已确认');
        }
      });
    });
  }

  function wireImpact() {
    const form = document.getElementById('impact-form');
    if (!form) return;
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const source = document.getElementById('im-source').value;
      const metric = document.getElementById('im-metric').value.trim();
      const target = document.getElementById('impact-result');
      target.textContent = '计算中…';
      try {
        const response = await fetch(
          '/api/sourcing/impact?source=' + encodeURIComponent(source) +
          '&metric=' + encodeURIComponent(metric));
        const body = await response.json();
        if (!response.ok) {
          target.textContent = errorText(body, response.status);
          return;
        }
        const lines = [];
        lines.push('现网版本 v' + body.revision + ' ｜ ' +
          body.source_label + ' / ' + body.metric);
        lines.push('下游节点 ' + body.downstream_count +
          ' ｜ 受影响报表 ' + body.report_count +
          ' ｜ 权重合计 ' + body.weight_sum + ' ｜ 影响分 ' + body.score);
        (body.affected_reports || []).forEach((r) => {
          lines.push('  ' + r.source + ' ' + r.report_id + ' ' +
            r.report_name + '（' + r.metric + '）w=' + r.weight);
        });
        target.innerHTML = '<pre class="ascii">' +
          lines.map((t) => t.replace(/&/g, '&amp;')).join('\n') + '</pre>';
      } catch (err) {
        target.textContent = '网络失败，检查服务是否在 18206';
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    wireButtons();
    wireImpact();
  });
})();
