import os
import sys
import time
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from archiver import run_organize_pipeline
from optimize_summaries import run_summary_optimization

def main():
    print("==================================================", flush=True)
    print("🚀 开始执行智能书库增量更新与数据优化流水线...", flush=True)
    print("   - 扫描新增文件：启用 Apple Vision OCR", flush=True)
    print("   - 比对 Excel 目录：清洗营销套话，精准注入简介与规范分类", flush=True)
    print("   - 重建索引：同步 SIMD 二进制向量高速缓存", flush=True)
    print("==================================================", flush=True)
    t0 = time.time()

    def _progress(st):
        print(
            f"  [扫描进度] 已扫描 {st['scanned_files']} | 新增藏书 {st['indexed_books']} | 合并重复 {st['duplicates_merged']} | 待审冲突 {st['conflicts_found']}",
            flush=True,
        )

    # 1. 增量扫描入库
    org_res = run_organize_pipeline(
        limit_per_dir=None,
        run_type="incremental_cli",
        enable_ocr=True,
        max_workers=8,
        progress_callback=_progress,
    )
    print("\n✅ 第一阶段：增量扫描入库完成：", flush=True)
    print(f"   - 发现文件: {org_res.get('total_discovered')} (已入库跳过: {org_res.get('already_indexed')})", flush=True)
    print(f"   - 新增入库藏书: {org_res.get('indexed_books')}", flush=True)
    print(f"   - 合并重复/多格式: {org_res.get('duplicates_merged')}", flush=True)
    print(f"   - 归档硬链接: {org_res.get('archived_links')}", flush=True)
    print(f"   - 待审冲突: {org_res.get('conflicts_found')}", flush=True)

    # 2. 简介精修与反套话优化
    print("\n🚀 第二阶段：比对《电子书分类索引与简介目录.xlsx》并执行简介反套话精修...", flush=True)
    opt_res = run_summary_optimization()
    print("✅ 第二阶段：简介与分类优化完成：", flush=True)
    print(f"   - 匹配 Excel 优质简介: {opt_res.get('matched_with_xlsx')}", flush=True)
    print(f"   - 双语书名修复: {opt_res.get('bilingual_repaired')}", flush=True)
    print(f"   - 更新藏书信息: {opt_res.get('updated_books')}", flush=True)
    print(f"   - 同步二进制向量: {opt_res.get('synced_vectors')}", flush=True)

    total_time = round(time.time() - t0, 2)
    print("\n==================================================", flush=True)
    print(f"🎉 全部数据更新完成！耗时: {total_time} 秒", flush=True)
    print("==================================================", flush=True)

if __name__ == "__main__":
    main()
