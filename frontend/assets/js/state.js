export const MOBILE_QUERY = '(max-width: 768px)';
export const IS_TOUCH_SCREEN = window.matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0;

export function isMobileReader() {
  return IS_TOUCH_SCREEN && window.matchMedia(MOBILE_QUERY).matches;
}

export const appState = {
  currentTab: 'shelf',
  searchTimer: null,
  isImeComposing: false,
  shelfAbortController: null,
  shelfReqSeq: 0,
  orgPollTimer: null,
  shelfSortTouched: false,
  shelfLastQuery: '',
  shelfLastTotal: 0,
  shelfIsAppending: false,
  shelfLoadFailed: false,
  shelfSheetKind: '',
  shelfLoadMoreObserver: null,
  interestReqSeq: 0,
  currentActiveBookDetail: null,
  chromeScrollTop: 0,
  readerWakeLock: null,
  pdfLongPressAt: 0,
  progressTimer: null,
  pendingProgress: null,
  htmlScrollPending: false,
  htmlScrollHandler: null,
  readerSelectionBubble: null,
  readerTransPopover: null,
  readerTransHistory: [],
  currentDrawerTrans: null,
  splitResizeTimer: null,
  libraryCounts: /** @type {Object<string, Object<string, number>>} */ ({}),  // per filter value, from /api/stats
};

export const PHONE_PAGE_SIZE = 24;
// The desktop list rows are short, so a page holds twice as many books as the phone's card batches
export const HOME_PAGE_SIZE = 50;

export function shelfPageSize() {
  return window.matchMedia(MOBILE_QUERY).matches ? PHONE_PAGE_SIZE : HOME_PAGE_SIZE;
}

export const shelfFilters = {
  q: '',
  category: '',
  language: '',
  format: '',
  source_dir: '',
  reading_status: '',
  pub_period: '',
  only_favorite: false,
  has_rating: false,
  author: '',  // one person from a book's author line: lists every book naming them
  page: 1,
  page_size: shelfPageSize(),
};

export const langSelections = {
  shelf: new Set(),
  interest: new Set(),
};

export const readerState = {
  isOpen: false,
  bookId: null,
  mode: 'html',
  pdfUrl: '',
  toc: [],
  chapters: [],
  currentChapter: 0,
  totalChapters: 1,
  activeAnchor: '',
  theme: localStorage.getItem('lib_reader_theme')
    || (isMobileReader() && !window.matchMedia('(prefers-color-scheme: dark)').matches ? 'light' : 'dark'),
  font: localStorage.getItem('lib_reader_font') || '',
  lineHeight: parseFloat(localStorage.getItem('lib_reader_lineheight') || '0'),
  fontSize: parseInt(localStorage.getItem('lib_reader_fontsize') || '18', 10),
  transFontSize: parseInt(localStorage.getItem('lib_reader_trans_fontsize') || '15', 10),
  wide: localStorage.getItem('lib_reader_wide') === '1',
  transOnly: localStorage.getItem('lib_reader_trans_only') === '1',
};

export const PDF_MAX_PIXEL_RATIO = 2;
export const PDF_KEPT_PAGE_RADIUS = 4;
export const PDF_RANGE_CHUNK_BYTES = 256 * 1024;
export const PDF_IMAGE_SETTLE_MS = 120;
export const PDF_IMAGE_ZOOM_MIN = 0.5;
export const PDF_IMAGE_ZOOM_MAX = 2;
export const PDF_LONG_PRESS_MS = 550;
export const PDF_LONG_PRESS_MOVE_PX = 10;
export const PDF_LONG_PRESS_TAP_GUARD_MS = 700;
export const MOBILE_NOTICE_MS = 2500;
export const UNDO_TOAST_MS = 5000;

export const pdfState = {
  doc: null,
  imageMode: false,
  imageVersion: '',
  scale: 1.15,
  pageCount: 0,
  currentPage: 1,
  currentTocIdx: 0,
  renderedPages: new Set(),
  renderingPages: new Set(),
  bilingualPages: new Set(),
  pageTranslations: new Map(),
  textLayerTasks: new Map(),
  observer: null,
  scrollHandler: null,
  isJumping: false,
};

export const AI_FALLBACK_TEXT = {
  nokey: '未配置 DeepSeek API Key，已保留标准检索',
  cap: '已达今日 AI 预算上限，已保留标准检索',
  empty: '无可重排结果，已保留标准检索',
  error: 'AI 精排暂时不可用，已保留标准检索',
};

export const CATEGORIES = [
  '全部',
  '历史与地缘政治',
  '文学小说与艺术',
  '商业管理与创投',
  '个人成长与生活',
  '社科政治与法律',
  '自然科学与健康',
  '金融与经济学',
  '哲学宗教与通识',
  '心理学与脑科学',
  '数学与统计',
  '人工智能与技术',
  '人物传记与回忆录',
  '销售与市场营销',
  '产品与UX设计',
];

export const COVER_THUMB_WIDTH = 360;
export const COVER_DETAIL_WIDTH = 720;
export const SHELF_MAX_PAGE_SIZE = 200;
export const SHEET_CLOSE_DRAG_PX = 72;

export const SHELF_STATUS_OPTIONS = [
  ['fav', '收藏'], ['reading', '在读'], ['want', '想读'], ['read', '已读'], ['has_rating', '有评分/书评'],
];
export const QUICK_STATUS_KEYS = ['fav', 'reading', 'want', 'read'];
export const SHELF_FILTER_GROUPS = [
  { key: 'status', title: '状态', options: SHELF_STATUS_OPTIONS },
  { key: 'category', title: '分类', options: CATEGORIES.slice(1).map(c => [c, c]) },
  { key: 'language', title: '语言', options: [['zh-CN', '简体中文'], ['zh-TW', '繁体中文'], ['en', '英文']] },
  { key: 'format', title: '格式', options: [['epub', 'EPUB'], ['pdf', 'PDF'], ['azw3', 'AZW3'], ['mobi', 'MOBI']] },
  { key: 'pub_period', title: '年代', options: [['thisyear', '今年'], ['2020s', '2020年代'], ['2010s', '2010年代'], ['2000s', '2000年代'], ['1900s', '20世纪'], ['pre1900', '早期经典']] },
  { key: 'source_dir', title: '来源', options: /** @type {string[][]} */ ([]) }  // filled from /api/stats: the source folders are this machine's config,
];
export const SORT_SHORT_LABELS = {
  imported: '导入时间（新→旧）', imported_asc: '导入时间（旧→新）', opened: '最近打开', relevance: '相关度', pub_desc: '出版时间（新→旧）', pub_asc: '出版时间（旧→新）',
  classic: '权威经典度', quality: '元数据完备度', rating: '我的评分', title: '书名音序',
};

export const RATING_SOURCE_NAMES = { douban: '豆瓣', google_books: 'Google Books', open_library: 'OpenLibrary', amazon: 'Amazon' };
export const READING_STATUS_LABEL = { unread: '未读', want: '想读', reading: '在读', read: '已读' };
export const RUN_TYPE_LABELS = { cli: '命令行', incremental_cli: '命令行增量', manual: '手动', scheduled: '定时', split: '拆分' };
export const RUN_STATUS_LABELS = { completed: '完成', running: '进行中', done: '已拆分' };
export const CONFLICT_LABELS = { title: '书名冲突', author: '作者冲突', category: '分类冲突', ambiguous_match: '同书待确认',
  ambiguous_duplicate: '疑似同名异书', missing_author: '缺少作者', corrupted_title: '书名残缺', stem_mismatch: '无法确定是否同一本' };

export const MOBILE_FONTS = [
  ['sans', '黑体'],
  ['serif', '宋体'],
  ['kaiti', '楷体'],
  ['fangsong', '仿宋'],
];
export const MOBILE_LINE_HEIGHTS = [['1.55', '紧凑'], ['1.75', '标准'], ['1.95', '宽松']];
export const MOBILE_DEFAULT_LINE_HEIGHT = 1.75;
export const CHROME_HIDE_SCROLL_PX = 24;
export const MOBILE_SHEET_IDS = { progress: 'm-sheet-progress', theme: 'm-sheet-theme', font: 'm-sheet-font', pagetext: 'm-sheet-pagetext' };

export const LATIN_TEXT_SAMPLE_CHARS = 1500;
export const LATIN_TO_CJK_RATIO = 2;

function splitPicked(raw) {
  return String(raw || '').split(',').filter(Boolean);
}

/** The values picked in one shelf filter group; every group can hold several, sent to the server comma-joined. */
export function shelfGroupValues(key) {
  if (key === 'status') {
    return [shelfFilters.only_favorite ? 'fav' : '', shelfFilters.has_rating ? 'has_rating' : '',
      ...splitPicked(shelfFilters.reading_status)].filter(Boolean);
  }
  if (key === 'language') return Array.from(langSelections.shelf);
  return splitPicked(shelfFilters[key]);
}
