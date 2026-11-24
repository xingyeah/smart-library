from http_api.handlers import books, connection, marks, organize, pdf_pages, reading, recommend, tonight, translate
from http_api.router import Router


def build_default_router() -> Router:
    """Instantiate and populate the standard declarative HTTP router."""
    router = Router()

    # GET routes
    router.add("GET", r"/api/stats", books.handle_get_stats)
    router.add("GET", r"/api/ping", connection.handle_get_ping)
    router.add("GET", r"/api/connection", connection.handle_get_connection)
    router.add("GET", r"/api/ai/usage", books.handle_get_ai_usage)
    router.add("GET", r"/api/books", books.handle_get_books)
    router.add("GET", r"/api/books/(?P<book_id>\d+)", books.handle_get_book_detail)
    router.add("GET", r"/api/books/(?P<book_id>\d+)/similar", books.handle_get_similar_books)
    router.add("GET", r"/api/books/(?P<book_id>\d+)/read", reading.handle_get_reader_content)
    router.add("GET", r"/api/books/(?P<book_id>\d+)/epub_asset", reading.handle_get_epub_asset)
    router.add("GET", r"/api/books/(?P<book_id>\d+)/mobi_image", reading.handle_get_mobi_image)
    router.add("GET", r"/api/books/(?P<book_id>\d+)/(?P<mode>stream|download)", reading.handle_get_book_file)
    router.add("GET", r"/api/books/(?P<book_id>\d+)/pdf_layout", pdf_pages.handle_get_pdf_layout)
    router.add("GET", r"/api/books/(?P<book_id>\d+)/pdf_page/(?P<page_no>\d+)", pdf_pages.handle_get_pdf_page)
    router.add("GET", r"/api/books/(?P<book_id>\d+)/pdf_text/(?P<page_no>\d+)", pdf_pages.handle_get_pdf_text)
    router.add("GET", r"/api/recommend/interests", recommend.handle_get_recommend_interests)
    router.add("GET", r"/api/tonight", tonight.handle_get_tonight)
    router.add("GET", r"/api/conflicts", organize.handle_get_conflicts)
    router.add("GET", r"/api/organize/mismerged", organize.handle_get_mismerged_books)
    router.add("GET", r"/api/organize/status", organize.handle_get_organize_status)
    router.add("GET", r"/covers/(?P<filename>[^/]+)", reading.handle_get_cover)
    router.add("GET", r"/vendor/(?P<filename>[^/]+)", reading.handle_get_vendor)
    router.add("GET", r"/assets/.+", reading.handle_get_asset)
    router.add("GET", r"(/|/index\.html)", reading.handle_get_frontend_spa)

    # POST routes
    router.add("POST", r"/api/translate", translate.handle_post_translate)
    router.add("POST", r"/api/translate/batch", translate.handle_post_translate_batch)
    router.add("POST", r"/api/recommend/describe", recommend.handle_post_recommend_describe)
    router.add("POST", r"/api/search/ai_rerank", recommend.handle_post_search_ai_rerank)
    router.add("POST", r"/api/interests/config", recommend.handle_post_interests_config)
    router.add("POST", r"/api/recommend/interests/ai", recommend.handle_post_interests_ai)
    router.add("POST", r"/api/books/(?P<book_id>\d+)/progress", books.handle_post_book_progress)
    router.add("POST", r"/api/books/(?P<book_id>\d+)/meta", books.handle_post_book_meta)
    router.add("POST", r"/api/books/batch_meta", marks.handle_post_batch_meta)
    router.add("POST", r"/api/skips", tonight.handle_post_skip)
    router.add("POST", r"/api/skips/undo", tonight.handle_post_skip_undo)
    router.add("POST", r"/api/books/(?P<book_id>\d+)/enrich_refresh", books.handle_post_book_enrich_refresh)
    router.add("POST", r"/api/books/(?P<book_id>\d+)/delete", books.handle_post_book_delete)
    router.add("POST", r"/api/conflicts/(?P<conflict_id>\d+)/resolve", organize.handle_post_conflict_resolve)
    router.add("POST", r"/api/organize/start", organize.handle_post_organize_start)
    router.add("POST", r"/api/organize/split_mismerged", organize.handle_post_split_mismerged)
    router.add("POST", r"/api/organize/rollback", organize.handle_post_organize_rollback)

    return router
