// What the reader searched for, kept on the server so the phone and the desktop offer the same recent searches.

/** The newest searches, newest first; [] when the library cannot be reached. */
export async function fetchSearchHistory() {
  try {
    const data = await (await fetch('/api/search_history')).json();
    return (data.items || []).map(item => String(item.query));
  } catch (error) {
    console.error('search history failed to load', error);
    return [];
  }
}

async function postHistory(url, query) {
  try {
    const response = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query }),
    });
    if (!response.ok) console.error('search history request refused', url, response.status);
    return response.ok;
  } catch (error) {
    console.error('search history request failed', url, error);
    return false;
  }
}

/** Moves `query` to the front of the history. */
export function recordSearch(query) {
  return postHistory('/api/search_history', query);
}

/** Removes `query` from the history. */
export function deleteSearch(query) {
  return postHistory('/api/search_history/delete', query);
}
