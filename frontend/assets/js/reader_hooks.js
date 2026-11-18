// Calls that go "upward" in the reader's module layering (a low-level module needing a higher one).
// reader.js fills these in when it loads; importing the higher module directly would make the modules import each other.
/** @type {{
 *   openOnlineReader: (bookId: number, chapterIdx?: number|null, anchor?: string, targetTitle?: string, targetTocIdx?: number) => Promise<void>,
 *   closeOnlineReader: () => void,
 *   openMobileSheet: (name: string) => void,
 *   toggleHtmlBilingualMode: () => Promise<void>,
 *   translatePdfPage: (pageNo: number) => Promise<void>,
 *   extractPdfPageParagraphs: (page: any) => Promise<string[]>,
 * }} */
export const readerHooks = /** @type {any} */ ({});
