// Deleting several ticked books from the desktop home. Each book goes through the same server call as the detail's
// 删除本书, so its files move to data/removed_books/ and can be found again.
import { appState, shelfFilters } from './state.js';
import { closeBookModal, multiFileDeleteMessage, postBookDelete } from './book_modal.js';
import { loadShelfBooks } from './shelf.js';
import { loadStats } from './api.js';

/** @typedef {{ id: number, title: string }} BookToDelete */

/** A ticked book from another page is not in the list at hand; its title is asked for only when it is needed. */
async function titleOf(book) {
  if (book.title) return book.title;
  try {
    const detail = await (await fetch(`/api/books/${book.id}`)).json();
    return detail.clean_title || detail.title || '本书';
  } catch (err) {
    console.error('book title lookup failed', book.id, err);
    return '本书';
  }
}

/** 'deleted' | 'kept' (the reader declined a multi-file book) | 'failed'. A book with several files is shown its
 * file list first, since some of them may be a wrongly merged other book. */
async function deleteOneBook(book) {
  try {
    let res = await postBookDelete(book.id, null);
    if (res.status === 409 && res.data.needs_confirmation) {
      if (!confirm(multiFileDeleteMessage(await titleOf(book), res.data.files))) return 'kept';
      res = await postBookDelete(book.id, res.data.files.map(f => f.id));
    }
    if (res.status === 200 && res.data.ok) return 'deleted';
    console.error('batch delete refused', book.id, res.status, res.data.error);
  } catch (err) {
    console.error('batch delete failed', book.id, err);
  }
  return 'failed';
}

function deleteSummary(counts) {
  const parts = [`已删除 ${counts.deleted} 本，文件已移至 data/removed_books/`];
  if (counts.kept) parts.push(`保留 ${counts.kept} 本（多文件的书没有确认）`);
  if (counts.failed) parts.push(`${counts.failed} 本没删掉，请稍后再试`);
  return parts.join('\n');
}

/** Deletes `books` one by one after one confirmation; resolves the ids that were deleted. */
export async function deleteBooks(books) {
  if (!books.length || !confirm(`确定要删除选中的 ${books.length} 本书吗？\n\n文件会移到 data/removed_books/，可以找回；`
    + '书架和搜索里不再出现。关联多个文件的书会逐本再确认。')) return [];
  const counts = { deleted: 0, kept: 0, failed: 0 };
  const deletedIds = [];
  for (const book of books) {
    const outcome = await deleteOneBook(book);
    counts[outcome] += 1;
    if (outcome === 'deleted') deletedIds.push(book.id);
  }
  const shown = appState.currentActiveBookDetail;
  if (shown && deletedIds.includes(shown.id)) closeBookModal();
  alert(deleteSummary(counts));
  loadShelfBooks(shelfFilters.page);
  loadStats();
  return deletedIds;
}
