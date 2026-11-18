#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
#import <PDFKit/PDFKit.h>
#import <NaturalLanguage/NaturalLanguage.h>
#import <AppKit/AppKit.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>

static NLEmbedding *g_zhEmb = nil;
static NLEmbedding *g_enEmb = nil;

static void ensureEmbeddings() {
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        g_zhEmb = [NLEmbedding wordEmbeddingForLanguage:NLLanguageSimplifiedChinese];
        g_enEmb = [NLEmbedding wordEmbeddingForLanguage:NLLanguageEnglish];
    });
}

// 1. Compute 300-dim L2-normalized semantic vector for text
int mac_embed_text(const char *utf8_text, float *out_vec300) {
    if (!utf8_text || !out_vec300) return -1;
    @autoreleasepool {
        ensureEmbeddings();
        for (int i = 0; i < 300; i++) out_vec300[i] = 0.0f;
        NSString *text = [NSString stringWithUTF8String:utf8_text];
        if (!text || text.length == 0) return 0;

        NLTokenizer *tok = [[NLTokenizer alloc] initWithUnit:NLTokenUnitWord];
        tok.string = text;
        __block int count = 0;
        NSUInteger len = MIN(text.length, (NSUInteger)8000);
        [tok enumerateTokensInRange:NSMakeRange(0, len) usingBlock:^(NSRange tokenRange, NLTokenizerAttributes flags, BOOL *stop) {
            NSString *word = [[text substringWithRange:tokenRange] lowercaseString];
            if (word.length == 0) return;
            // Higher weight for title, tags, and core thesis at the beginning of text
            float weight = (tokenRange.location < 60) ? 3.0f : ((tokenRange.location < 220) ? 1.8f : 1.0f);
            if (word.length >= 2) weight *= 1.3f;

            NSArray<NSNumber *> *v = [g_zhEmb vectorForString:word];
            if (!v && g_enEmb) {
                v = [g_enEmb vectorForString:word];
            }
            if (v && v.count == 300) {
                for (int i = 0; i < 300; i++) {
                    out_vec300[i] += v[i].floatValue * weight;
                }
                count++;
            }
        }];

        double norm = 0.0;
        for (int i = 0; i < 300; i++) norm += (double)out_vec300[i] * (double)out_vec300[i];
        norm = sqrt(norm);
        if (norm > 1e-8) {
            for (int i = 0; i < 300; i++) out_vec300[i] = (float)(out_vec300[i] / norm);
        }
        return count;
    }
}

// 2. Tokenize query and expand semantic neighbor words via NLEmbedding
int mac_expand_query(const char *utf8_query, char *out_json, int max_len) {
    if (!utf8_query || !out_json || max_len <= 2) return -1;
    @autoreleasepool {
        ensureEmbeddings();
        NSString *query = [NSString stringWithUTF8String:utf8_query];
        if (!query) return -1;

        NSSet *stopwords = [NSSet setWithArray:@[
            @"的", @"了", @"和", @"与", @"或", @"是", @"在", @"有", @"我", @"想", @"要",
            @"看", @"找", @"读", @"一本", @"关于", @"讲解", @"介绍", @"推荐", @"书籍",
            @"书", @"相关", @"相关的", @"有关", @"哪些", @"什么", @"如何", @"怎么", @"最", @"好", @"比较",
            @"一个", @"一些", @"之", @"及", @"等", @"中", @"上", @"下", @"能", @"可以",
            @"给出", @"最准", @"本", @"讲", @"类似", @"像", @"那种", @"非常", @"特别",
            @"我想", @"想看", @"看看", @"寻找", @"读物", @"书目", @"著作", @"作品",
            @"硬核", @"深度", @"通俗", @"易懂", @"深入", @"浅出"
        ]];

        // Broad parent discipline suffixes that should not expand into sibling branches when preceded by a specific qualifier
        NSSet *broadParentNouns = [NSSet setWithArray:@[
            @"数学", @"物理", @"物理学", @"化学", @"生物", @"生物学", @"心理学", @"经济学", @"历史", @"哲学", @"科学", @"工程", @"技术", @"理论", @"教材", @"教程", @"导论", @"入门", @"指南", @"基础"
        ]];

        NLTokenizer *tok = [[NLTokenizer alloc] initWithUnit:NLTokenUnitWord];
        tok.string = query;

        NSMutableArray<NSString *> *tokens = [NSMutableArray array];
        NSMutableDictionary<NSString *, NSNumber *> *neighborMap = [NSMutableDictionary dictionary];

        [tok enumerateTokensInRange:NSMakeRange(0, query.length) usingBlock:^(NSRange tokenRange, NLTokenizerAttributes flags, BOOL *stop) {
            NSString *word = [[query substringWithRange:tokenRange] lowercaseString];
            // Strictly reject single-character fragments (e.g. 硬, 核, 论) and stopwords
            if (word.length < 2 || [stopwords containsObject:word]) return;
            if (![tokens containsObject:word]) {
                [tokens addObject:word];
            }
            // Skip NLEmbedding neighbor expansion for overly broad parent nouns if the query already has specific modifiers
            if ([broadParentNouns containsObject:word] && query.length >= 4) {
                return;
            }
            NLEmbedding *emb = [g_zhEmb containsString:word] ? g_zhEmb : ([g_enEmb containsString:word] ? g_enEmb : nil);
            if (emb) {
                [emb enumerateNeighborsForString:word maximumCount:6 distanceType:NLDistanceTypeCosine usingBlock:^(NSString *neighbor, NLDistance distance, BOOL *stop) {
                    double sim = 1.0 - (distance * distance) / 2.0;
                    if (sim > 0.52 && ![stopwords containsObject:neighbor] && ![broadParentNouns containsObject:neighbor] && neighbor.length >= 2) {
                        NSNumber *existing = neighborMap[neighbor];
                        if (!existing || sim > existing.doubleValue) {
                            neighborMap[neighbor] = @(round(sim * 1000.0) / 1000.0);
                        }
                    }
                }];
            }
        }];

        NSDictionary *res = @{
            @"tokens": tokens,
            @"neighbors": neighborMap
        };
        NSData *data = [NSJSONSerialization dataWithJSONObject:res options:0 error:nil];
        if (!data) return -1;
        NSUInteger copyLen = MIN(data.length, (NSUInteger)(max_len - 1));
        memcpy(out_json, data.bytes, copyLen);
        out_json[copyLen] = '\0';
        return (int)copyLen;
    }
}

static void collectOutline(PDFOutline *outline, NSMutableArray<NSString *> *items, int depth) {
    if (!outline || items.count >= 60 || depth > 3) return;
    NSString *label = [outline.label stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (label && label.length > 0) {
        [items addObject:label];
    }
    NSUInteger n = outline.numberOfChildren;
    for (NSUInteger i = 0; i < n; i++) {
        if (items.count >= 60) break;
        collectOutline([outline childAtIndex:i], items, depth + 1);
    }
}

static NSString *ocrPageImage(PDFPage *page) {
    if (!page) return @"";
    NSRect bounds = [page boundsForBox:kPDFDisplayBoxMediaBox];
    if (bounds.size.width <= 10 || bounds.size.height <= 10) return @"";
    CGFloat scale = 1200.0 / MAX(bounds.size.width, bounds.size.height);
    if (scale < 1.0) scale = 1.0;
    if (scale > 2.2) scale = 2.2;
    NSSize imgSize = NSMakeSize(bounds.size.width * scale, bounds.size.height * scale);

    NSImage *image = [page thumbnailOfSize:imgSize forBox:kPDFDisplayBoxMediaBox];
    if (!image) return @"";
    CGImageRef cgImg = [image CGImageForProposedRect:NULL context:nil hints:nil];
    if (!cgImg) return @"";

    __block NSMutableArray<NSString *> *lines = [NSMutableArray array];
    VNRecognizeTextRequest *req = [[VNRecognizeTextRequest alloc] initWithCompletionHandler:^(VNRequest *request, NSError *error) {
        if (error) return;
        for (VNRecognizedTextObservation *obs in request.results) {
            VNRecognizedText *top = [[obs topCandidates:1] firstObject];
            if (top && top.string.length > 0) {
                [lines addObject:top.string];
            }
        }
    }];
    req.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
    req.recognitionLanguages = @[@"zh-Hans", @"zh-Hant", @"en-US"];
    req.usesLanguageCorrection = YES;

    VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:cgImg options:@{}];
    [handler performRequests:@[req] error:nil];
    return [lines componentsJoinedByString:@"\n"];
}

// 3. Parse PDF metadata, outline (TOC), first pages text, optional Apple Vision OCR, and cover thumbnail
int mac_parse_pdf(const char *utf8_pdf_path, const char *utf8_cover_out, int enable_ocr, char *out_json, int max_len) {
    if (!utf8_pdf_path || !out_json || max_len <= 2) return -1;
    @autoreleasepool {
        NSString *pdfPath = [NSString stringWithUTF8String:utf8_pdf_path];
        NSURL *url = [NSURL fileURLWithPath:pdfPath];
        PDFDocument *doc = [[PDFDocument alloc] initWithURL:url];
        if (!doc) {
            snprintf(out_json, max_len, "{\"ok\":false,\"error\":\"cannot_open_pdf\"}");
            return 0;
        }

        NSDictionary *attrs = doc.documentAttributes ?: @{};
        NSString *title = attrs[PDFDocumentTitleAttribute] ?: @"";
        NSString *author = attrs[PDFDocumentAuthorAttribute] ?: @"";
        NSString *subject = attrs[PDFDocumentSubjectAttribute] ?: @"";
        id kwObj = attrs[PDFDocumentKeywordsAttribute];
        NSString *keywords = @"";
        if ([kwObj isKindOfClass:[NSString class]]) keywords = kwObj;
        else if ([kwObj isKindOfClass:[NSArray class]]) keywords = [(NSArray *)kwObj componentsJoinedByString:@", "];

        NSMutableArray<NSString *> *toc = [NSMutableArray array];
        if (doc.outlineRoot) {
            collectOutline(doc.outlineRoot, toc, 0);
        }

        NSUInteger pageCount = doc.pageCount;
        NSUInteger maxTextPages = MIN(pageCount, (NSUInteger)10);
        NSMutableString *extractedText = [NSMutableString string];
        for (NSUInteger i = 0; i < maxTextPages; i++) {
            PDFPage *p = [doc pageAtIndex:i];
            NSString *s = p.string;
            if (s && s.length > 0) {
                [extractedText appendString:s];
                [extractedText appendString:@"\n"];
                if (extractedText.length > 4000) break;
            }
        }

        NSString *trimmed = [extractedText stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        BOOL usedOCR = NO;
        if (trimmed.length < 60 && enable_ocr && pageCount > 0) {
            usedOCR = YES;
            NSUInteger ocrPages = MIN(pageCount, (NSUInteger)5);
            for (NSUInteger i = 0; i < ocrPages; i++) {
                NSString *ocrTxt = ocrPageImage([doc pageAtIndex:i]);
                if (ocrTxt.length > 0) {
                    [extractedText appendFormat:@"[Page %lu OCR]\n%@\n", (unsigned long)(i + 1), ocrTxt];
                }
                if (extractedText.length > 3000) break;
            }
        }

        BOOL coverSaved = NO;
        if (utf8_cover_out && strlen(utf8_cover_out) > 0 && pageCount > 0) {
            PDFPage *firstPage = [doc pageAtIndex:0];
            if (firstPage) {
                NSImage *thumb = [firstPage thumbnailOfSize:NSMakeSize(480, 680) forBox:kPDFDisplayBoxMediaBox];
                if (thumb) {
                    CGImageRef cgRef = [thumb CGImageForProposedRect:NULL context:nil hints:nil];
                    if (cgRef) {
                        NSBitmapImageRep *rep = [[NSBitmapImageRep alloc] initWithCGImage:cgRef];
                        NSData *jpg = [rep representationUsingType:NSBitmapImageFileTypeJPEG properties:@{NSImageCompressionFactor: @(0.82)}];
                        if (jpg) {
                            NSString *coverPath = [NSString stringWithUTF8String:utf8_cover_out];
                            coverSaved = [jpg writeToFile:coverPath atomically:YES];
                        }
                    }
                }
            }
        }

        NSString *sampleText = extractedText.length > 3500 ? [extractedText substringToIndex:3500] : extractedText;
        NSDictionary *res = @{
            @"ok": @YES,
            @"title": title ?: @"",
            @"author": author ?: @"",
            @"subject": subject ?: @"",
            @"keywords": keywords ?: @"",
            @"page_count": @(pageCount),
            @"toc": toc,
            @"text_sample": sampleText ?: @"",
            @"used_ocr": @(usedOCR),
            @"cover_saved": @(coverSaved)
        };
        NSData *data = [NSJSONSerialization dataWithJSONObject:res options:0 error:nil];
        if (!data) return -1;
        NSUInteger copyLen = MIN(data.length, (NSUInteger)(max_len - 1));
        memcpy(out_json, data.bytes, copyLen);
        out_json[copyLen] = '\0';
        return (int)copyLen;
    }
}

// 4. Fast C SIMD dot-product search over N x 300 L2-normalized float matrix
int mac_batch_cosine_scores(const float *query_vec300, const float *matrix_nx300, int num_rows, float *out_scores) {
    if (!query_vec300 || !matrix_nx300 || !out_scores || num_rows <= 0) return -1;
    for (int r = 0; r < num_rows; r++) {
        const float *row = matrix_nx300 + (r * 300);
        float sum = 0.0f;
        for (int c = 0; c < 300; c++) {
            sum += query_vec300[c] * row[c];
        }
        out_scores[r] = sum;
    }
    return num_rows;
}

// 5. Convert Chinese text to full Pinyin + Pinyin initials for instant search
int mac_to_pinyin(const char *utf8_text, char *out_buf, int max_len) {
    if (!utf8_text || !out_buf || max_len <= 2) return -1;
    @autoreleasepool {
        NSString *s = [NSString stringWithUTF8String:utf8_text];
        if (!s || s.length == 0) {
            out_buf[0] = '\0';
            return 0;
        }
        if (s.length > 300) s = [s substringToIndex:300];
        NSString *latin = [[s stringByApplyingTransform:NSStringTransformMandarinToLatin reverse:NO]
                           stringByApplyingTransform:NSStringTransformStripCombiningMarks reverse:NO].lowercaseString;
        if (!latin) {
            out_buf[0] = '\0';
            return 0;
        }
        NSArray<NSString *> *parts = [latin componentsSeparatedByCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        NSMutableString *joined = [NSMutableString string];
        NSMutableString *initials = [NSMutableString string];
        for (NSString *p in parts) {
            if (p.length > 0) {
                unichar c = [p characterAtIndex:0];
                if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) {
                    [joined appendString:p];
                    [initials appendFormat:@"%C", c];
                }
            }
        }
        NSString *result = [NSString stringWithFormat:@"%@ %@ %@", latin, joined, initials];
        NSData *data = [result dataUsingEncoding:NSUTF8StringEncoding];
        NSUInteger copyLen = MIN(data.length, (NSUInteger)(max_len - 1));
        memcpy(out_buf, data.bytes, copyLen);
        out_buf[copyLen] = '\0';
        return (int)copyLen;
    }
}

static void collectRichOutline(PDFDocument *doc, PDFOutline *outline, NSMutableArray<NSDictionary *> *items, int depth) {
    if (!outline || items.count >= 300 || depth > 4) return;
    NSString *label = [outline.label stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (label && label.length > 0) {
        NSUInteger pageIdx = 0;
        if (outline.destination && outline.destination.page) {
            NSUInteger idx = [doc indexForPage:outline.destination.page];
            if (idx != NSNotFound) pageIdx = idx;
        }
        [items addObject:@{
            @"title": label,
            @"page": @(pageIdx + 1),
            @"level": @(depth > 0 ? depth - 1 : 0)
        }];
    }
    NSUInteger n = outline.numberOfChildren;
    for (NSUInteger i = 0; i < n; i++) {
        if (items.count >= 300) break;
        collectRichOutline(doc, [outline childAtIndex:i], items, depth + 1);
    }
}

// 6. Fast PDF hierarchical outline (TOC) with exact page numbers for reader sidebar
int mac_pdf_outline(const char *utf8_pdf_path, char *out_json, int max_len) {
    if (!utf8_pdf_path || !out_json || max_len <= 2) return -1;
    @autoreleasepool {
        NSString *pdfPath = [NSString stringWithUTF8String:utf8_pdf_path];
        NSURL *url = [NSURL fileURLWithPath:pdfPath];
        PDFDocument *doc = [[PDFDocument alloc] initWithURL:url];
        if (!doc) {
            snprintf(out_json, max_len, "{\"ok\":false}");
            return 0;
        }
        NSMutableArray<NSDictionary *> *toc = [NSMutableArray array];
        if (doc.outlineRoot) {
            collectRichOutline(doc, doc.outlineRoot, toc, 0);
        }
        NSDictionary *res = @{
            @"ok": @YES,
            @"page_count": @(doc.pageCount),
            @"toc": toc
        };
        NSData *data = [NSJSONSerialization dataWithJSONObject:res options:0 error:nil];
        if (!data) return -1;
        NSUInteger copyLen = MIN(data.length, (NSUInteger)(max_len - 1));
        memcpy(out_json, data.bytes, copyLen);
        out_json[copyLen] = '\0';
        return (int)copyLen;
    }
}


// 7. Page images and text for the phone reader, which shows server-rendered pages instead of running pdf.js.
// PDFKit documents are not safe to draw from several threads, and reopening a large PDF per page costs more than
// drawing it, so one document stays open and every call is serialized on it.
static NSString *g_openPdfKey = nil;
static PDFDocument *g_openPdfDoc = nil;
static NSObject *g_pdfLock = nil;

// Keyed by path, size and modification time: a file replaced at the same path must be read again
static PDFDocument *openSharedPdf(NSString *path) {
    NSDictionary *attrs = [[NSFileManager defaultManager] attributesOfItemAtPath:path error:nil];
    if (!attrs) return nil;
    NSString *key = [NSString stringWithFormat:@"%@|%llu|%f", path, attrs.fileSize,
                     attrs.fileModificationDate.timeIntervalSince1970];
    if (![key isEqualToString:g_openPdfKey]) {
        g_openPdfDoc = [[PDFDocument alloc] initWithURL:[NSURL fileURLWithPath:path]];
        g_openPdfKey = g_openPdfDoc ? key : nil;
    }
    return g_openPdfDoc;
}

static NSObject *pdfLock(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{ g_pdfLock = [NSObject new]; });
    return g_pdfLock;
}

static NSSize pageDisplaySize(PDFPage *page) {
    NSSize size = [page boundsForBox:kPDFDisplayBoxCropBox].size;
    NSInteger rotation = ((page.rotation % 360) + 360) % 360;
    return (rotation == 90 || rotation == 270) ? NSMakeSize(size.height, size.width) : size;
}

static int copyJson(id obj, char *out_json, int max_len) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:obj options:0 error:nil];
    if (!data || data.length >= (NSUInteger)max_len) return -1;
    memcpy(out_json, data.bytes, data.length);
    out_json[data.length] = '\0';
    return (int)data.length;
}

// Writes {"ok":true,"sizes":[[w,h],...]} in points; -1 when the buffer is too small.
int mac_pdf_page_sizes(const char *utf8_pdf_path, char *out_json, int max_len) {
    if (!utf8_pdf_path || !out_json || max_len <= 2) return -1;
    @autoreleasepool {
        @synchronized (pdfLock()) {
            PDFDocument *doc = openSharedPdf([NSString stringWithUTF8String:utf8_pdf_path]);
            if (!doc || doc.isLocked) return copyJson(@{@"ok": @NO}, out_json, max_len);
            NSMutableArray *sizes = [NSMutableArray arrayWithCapacity:doc.pageCount];
            for (NSUInteger i = 0; i < doc.pageCount; i++) {
                NSSize size = pageDisplaySize([doc pageAtIndex:i]);
                [sizes addObject:@[@(lround(size.width)), @(lround(size.height))]];
            }
            return copyJson(@{@"ok": @YES, @"sizes": sizes}, out_json, max_len);
        }
    }
}

// Renders page `page_index` (0-based) `width_px` wide on white and writes a JPEG; 1 on success, 0 on failure.
int mac_pdf_render_page(const char *utf8_pdf_path, int page_index, int width_px, float jpeg_quality, const char *utf8_out_path) {
    if (!utf8_pdf_path || !utf8_out_path || width_px <= 0) return 0;
    @autoreleasepool {
        CGImageRef image = NULL;
        @synchronized (pdfLock()) {
            PDFDocument *doc = openSharedPdf([NSString stringWithUTF8String:utf8_pdf_path]);
            if (!doc || doc.isLocked || page_index < 0 || (NSUInteger)page_index >= doc.pageCount) return 0;
            PDFPage *page = [doc pageAtIndex:page_index];
            NSSize size = pageDisplaySize(page);
            if (size.width <= 0 || size.height <= 0) return 0;
            CGFloat scale = width_px / size.width;
            size_t height_px = (size_t)lround(size.height * scale);
            CGColorSpaceRef space = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
            CGContextRef ctx = CGBitmapContextCreate(NULL, width_px, height_px, 8, 0, space, (CGBitmapInfo)kCGImageAlphaNoneSkipLast);
            CGColorSpaceRelease(space);
            if (!ctx) return 0;
            CGContextSetRGBFillColor(ctx, 1, 1, 1, 1);
            CGContextFillRect(ctx, CGRectMake(0, 0, width_px, height_px));
            CGContextSetInterpolationQuality(ctx, kCGInterpolationHigh);
            CGContextScaleCTM(ctx, scale, scale);
            [page drawWithBox:kPDFDisplayBoxCropBox toContext:ctx];
            image = CGBitmapContextCreateImage(ctx);
            CGContextRelease(ctx);
        }
        if (!image) return 0;
        NSBitmapImageRep *rep = [[NSBitmapImageRep alloc] initWithCGImage:image];
        CGImageRelease(image);
        NSData *jpg = [rep representationUsingType:NSBitmapImageFileTypeJPEG properties:@{NSImageCompressionFactor: @(jpeg_quality)}];
        return (jpg && [jpg writeToFile:[NSString stringWithUTF8String:utf8_out_path] atomically:YES]) ? 1 : 0;
    }
}

// Plain text of page `page_index` (0-based), lines separated by '\n'; byte length written, -1 on failure.
int mac_pdf_page_text(const char *utf8_pdf_path, int page_index, char *out_text, int max_len) {
    if (!utf8_pdf_path || !out_text || max_len <= 1) return -1;
    @autoreleasepool {
        NSString *text = nil;
        @synchronized (pdfLock()) {
            PDFDocument *doc = openSharedPdf([NSString stringWithUTF8String:utf8_pdf_path]);
            if (!doc || doc.isLocked || page_index < 0 || (NSUInteger)page_index >= doc.pageCount) return -1;
            text = [doc pageAtIndex:page_index].string ?: @"";
        }
        const char *utf8 = text.UTF8String;
        size_t len = MIN(strlen(utf8), (size_t)(max_len - 1));
        memcpy(out_text, utf8, len);
        out_text[len] = '\0';
        return (int)len;
    }
}

// Lines of page `page_index` (0-based) with their boxes in points from the page's top-left:
// {"ok":true,"width":w,"height":h,"lines":[{"t":text,"x":..,"y":..,"w":..,"h":..},...]}; -1 when the buffer is too
// small. The box height stands in for the font size: PDFSelection.attributedString logs debug lines on every call.
// Rotated pages report unrotated boxes (the bilingual layout then only loses its alignment).
int mac_pdf_page_lines(const char *utf8_pdf_path, int page_index, char *out_json, int max_len) {
    if (!utf8_pdf_path || !out_json || max_len <= 2) return -1;
    @autoreleasepool {
        @synchronized (pdfLock()) {
            PDFDocument *doc = openSharedPdf([NSString stringWithUTF8String:utf8_pdf_path]);
            if (!doc || doc.isLocked || page_index < 0 || (NSUInteger)page_index >= doc.pageCount) {
                return copyJson(@{@"ok": @NO}, out_json, max_len);
            }
            PDFPage *page = [doc pageAtIndex:page_index];
            NSRect crop = [page boundsForBox:kPDFDisplayBoxCropBox];
            NSMutableArray *lines = [NSMutableArray array];
            PDFSelection *all = [page selectionForRect:crop];
            for (PDFSelection *line in (all ? all.selectionsByLine : @[])) {
                NSString *text = [line.string stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
                if (text.length == 0) continue;
                NSRect box = [line boundsForPage:page];
                [lines addObject:@{
                    @"t": text,
                    @"x": @(round((box.origin.x - crop.origin.x) * 10) / 10),
                    @"y": @(round((NSMaxY(crop) - NSMaxY(box)) * 10) / 10),
                    @"w": @(round(box.size.width * 10) / 10),
                    @"h": @(round(box.size.height * 10) / 10),
                }];
            }
            return copyJson(@{@"ok": @YES, @"width": @(crop.size.width), @"height": @(crop.size.height), @"lines": lines},
                            out_json, max_len);
        }
    }
}
