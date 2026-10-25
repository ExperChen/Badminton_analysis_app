const scripts = new Map();
export const path = value => window.YuxuPath(value);
export const go = value => location.assign(path(value));
export const count = n => Number(n || 0).toLocaleString('zh-CN');
export const duration = n => n == null ? '—' : `${Math.floor(n/60)}:${Math.floor(n%60).toString().padStart(2,'0')}`;
export const displayDate = value => value ? (/^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0,10) : value) : '训练视频';
export const term = (value, field) => window.AppText?.term(value, field) ?? String(value ?? '未标注');
export const sequence = value => window.AppText?.sequence(value) || String(value ?? '');
export const errorText = value => window.AppText?.error(value) || String(value ?? '操作未完成');
export const format = (value, suffix='') => value == null ? '—' : `${value}${suffix}`;
export const analysisNote = value => String(value);

export async function api(url, options = {}) {
  const response = await fetch(path(url), {cache:'no-store',...options});
  const data = await response.json().catch(()=>({}));
  if (!response.ok) throw new Error(errorText(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}）`));
  return data;
}
export function loadScript(src) {
  if (!scripts.has(src)) scripts.set(src,new Promise((resolve,reject)=>{
    const script=document.createElement('script');script.src=path(src);
    script.onload=resolve;script.onerror=()=>{scripts.delete(src);script.remove();reject(new Error('页面资源加载失败，请刷新重试'));};
    document.head.appendChild(script);
  }));
  return scripts.get(src);
}
export async function uploadVideo(file,options={}) {
  await loadScript('/static/video-upload.js');
  return window.YuxuUploadVideo(file,options);
}
