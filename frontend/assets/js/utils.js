import {
  appState, readerState, isMobileReader,
  COVER_THUMB_WIDTH, LATIN_TEXT_SAMPLE_CHARS, LATIN_TO_CJK_RATIO, MOBILE_NOTICE_MS,
} from './state.js';

export function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}


export function bookCoverImgHtml(b) {
  return b.cover_path
    ? `<img src="${escapeHtml(b.cover_path)}?w=${COVER_THUMB_WIDTH}" loading="lazy" decoding="async" onerror="if(!this.dataset.retried){this.dataset.retried='1';const u=this.src;setTimeout(()=>{this.src=u+'&retry=1'},400);}else{this.style.display='none';}" />`
    : '';
}


export function isMostlyLatinText() {
  const titles = (readerState.chapters || []).map(c => c.title || '').join(' ');
  const body = document.getElementById('reader-article-body').textContent || '';
  const sample = `${titles} ${body}`.slice(0, LATIN_TEXT_SAMPLE_CHARS);
  const latin = (sample.match(/[A-Za-z]/g) || []).length;
  const cjk = (sample.match(/[一-鿿]/g) || []).length;
  return latin > LATIN_TO_CJK_RATIO * cjk;
}

// Screen stays on while reading (Safari 16.4+); older browsers simply lack the API
export async function keepScreenOn() {
  if (!('wakeLock' in navigator) || appState.readerWakeLock || !readerState.isOpen) return;
  try {
    appState.readerWakeLock = await navigator.wakeLock.request('screen');
    appState.readerWakeLock.addEventListener('release', () => { appState.readerWakeLock = null; });
  } catch (e) {
    console.warn('Screen wake lock unavailable:', e.message);
  }
}

export function releaseScreenWakeLock() {
  if (!appState.readerWakeLock) return;
  appState.readerWakeLock.release().catch(e => console.warn('Screen wake lock release failed:', e.message));
  appState.readerWakeLock = null;
}

// The system drops the wake lock whenever the page is hidden; take it again on return
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && readerState.isOpen && isMobileReader()) keepScreenOn();
});


export function timeAgo(utcText) {
  const t = Date.parse(String(utcText).replace(' ', 'T') + 'Z');
  if (!t) return '';
  const min = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  if (min < 1440) return `${Math.round(min / 60)} 小时前`;
  return `${Math.round(min / 1440)} 天前`;
}


export function showMobileNotice(text) {
  const notice = document.createElement('div');
  notice.className = 'm-notice';
  notice.textContent = text;
  document.body.appendChild(notice);
  setTimeout(() => notice.remove(), MOBILE_NOTICE_MS);
}

