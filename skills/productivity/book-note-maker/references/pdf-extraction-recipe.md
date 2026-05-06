# PDF Text Extraction & Chapter Detection

## 系统要求检测

执行 PDF 提取前，先检查环境：

```bash
# 检查 pypdf
python3 -c "import pypdf; print('pypdf OK')" 2>&1
```

如果未安装，通过 hermetic venv 的 uv 安装：

```bash
uv pip install pypdf --quiet
```

## 文本提取决策树

```
┌─────────────────────────────────────┐
│        用户上传 PDF                  │
└────────────────┬────────────────────┘
                 ▼
┌─────────────────────────────────────┐
│   pypdf 提取文本                     │
└────────────────┬────────────────────┘
                 ▼
          chars < pages×20 ?
         ┌────┴────┐
        YES        NO
         ▼          ▼
   扫描版 PDF   文本型 PDF
   进入 OCR     继续分析
```

## 文本提取（pypdf）

```python
import pypdf

def extract_pdf_text(pdf_path: str) -> tuple[str, dict, int]:
    """提取PDF全文和元数据"""
    reader = pypdf.PdfReader(pdf_path)
    
    meta = {}
    if reader.metadata:
        for key in ['/Title', '/Author', '/Subject', '/Keywords',
                     '/Creator', '/Producer', '/CreationDate']:
            val = reader.metadata.get(key)
            if val:
                meta[key.lstrip('/')] = str(val)
    
    full_text = ''
    for page in reader.pages:
        text = page.extract_text()
        if text:
            full_text += text + '\n'
    
    return full_text, meta, len(reader.pages)


def is_scanned_pdf(pdf_path, min_ratio=20):
    """判断是否扫描版 PDF（纯图片）"""
    reader = pypdf.PdfReader(pdf_path)
    total_chars = sum(len(p.extract_text() or '') for p in reader.pages)
    pages = len(reader.pages)
    ratio = total_chars / max(pages, 1)
    return ratio < min_ratio, total_chars, pages
```

## OCR 流程（扫描版 PDF）

### 方案 A：marker-pdf（推荐，高质量 OCR）

```bash
# 安装（首次需要 ~5GB 空间）
uv pip install marker-pdf

# 执行 OCR
marker_single document.pdf --output_dir /tmp/book_ocr/ --languages zh,en

# 读取 OCR 结果
cat /tmp/book_ocr/document.md > /tmp/book_full_text.txt
```

**language 参数说明**：

| 语言 | 参数值 | 备注 |
|------|--------|------|
| 中文 | `zh` | 简体+繁体 |
| 英文 | `en` | 默认 |
| 中日双语 | `zh,ja` | |
| 中英双语 | `zh,en` | 最常见 |
| 多语言 | `zh,en,ja,ko` | 根据需要组合 |

### 方案 B：pymupdf（轻量级，无模型依赖）

如果 marker-pdf 因空间不足无法安装，pymupdf 可做基础 OCR：

```bash
uv pip install pymupdf
```

```python
import pymupdf
doc = pymupdf.open("document.pdf")
text = ""
for page in doc:
    text += page.get_text() + "\n"
```

> ⚠️ 注意：pymupdf 的 `get_text()` 仅提取嵌入文本层（对纯扫描版 PDF 无效）。真正的 OCR 需要 marker-pdf。

### 空间检查

```bash
# 检查可用空间
df -h /tmp
```

如果不足 5GB，告知用户：
> "此文档需要 OCR 提取，需安装 marker-pdf（~5GB）。当前可用空间 X GB。建议：清理空间后重试，或确认文档是否已包含文字层。"

## 章节检测（多语言增强版）

```python
import re

def detect_chapters(text: str, lines: list[str] = None) -> list[tuple[str, int, str]]:
    """
    检测章节边界，返回 [(标题, 行号, 标签), ...]
    支持中文/英文/双语模式
    """
    if lines is None:
        lines = text.split('\n')
    
    # ===== 多模式匹配 =====
    patterns = [
        (r'第[一二三四五六七八九十百零]+章\s*[^\n]*', 'cn_num'),
        (r'第\d+章\s*[^\n]*', 'cn_digit'),
        (r'[Cc][Hh][Aa][Pp][Tt][Ee][Rr]\s+\d+[^\n]*', 'en_chapter'),
        (r'[Pp][Aa][Rr][Tt]\s+[IVXLCDM\d]+[^\n]*', 'en_part'),
        (r'[Cc][Hh][Aa][Pp]\.?\s*\d+[^\n]*', 'en_variant'),
        (r'^\d+\.\s+[A-Z][^\n]{3,}', 'en_numbered'),
    ]
    
    all_matches = []
    
    # 逐行匹配
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        for pattern, label in patterns:
            if re.search(pattern, stripped):
                all_matches.append((stripped, i, label))
                break
    
    # 双语检测：中文 + 英文各自 ≥ 2 则合并
    cn_matches = [m for m in all_matches if m[2].startswith('cn')]
    en_matches = [m for m in all_matches if m[2].startswith('en')]
    
    if len(cn_matches) >= 2 and len(en_matches) >= 2:
        merged = cn_matches + en_matches
        merged.sort(key=lambda x: x[1])
        deduped = _deduplicate(merged)
        return deduped
    elif len(cn_matches) >= len(en_matches):
        deduped = _deduplicate(sorted(cn_matches, key=lambda x: x[1]))
        return deduped
    else:
        deduped = _deduplicate(sorted(en_matches, key=lambda x: x[1]))
        return deduped


def _deduplicate(matches, window=5):
    """去重：同 window 行内的视为同一章节"""
    seen_buckets = set()
    result = []
    for title, line_no, label in matches:
        bucket = line_no // window
        if bucket not in seen_buckets:
            seen_buckets.add(bucket)
            result.append((title, line_no, label))
    return result


def add_front_matter(text_lines, chapters, keywords=None):
    """添加序言/前言/引言等前置章节"""
    if keywords is None:
        keywords = ['序言', '前言', '引言', 'Preface', 'Introduction', 'Prologue']
    total_lines = len(text_lines)
    for kw in keywords:
        for i, line in enumerate(text_lines[:int(total_lines*0.15)]):
            if kw in line:
                chapters.insert(0, (kw, i, 'front_matter'))
                return chapters
    return chapters
```

## 处理换行断裂的中文文本

```python
# 平铺后重新匹配（解决中文PDF常见换行断裂）
flat_text = text.replace('\n', '')
pattern = r'第[一二三四五六七八九十百零]+章[^。；]*'
matches = list(re.finditer(pattern, flat_text))
```

## 分片存储（大文件）

```python
def split_into_chunks(text: str, chunk_size: int = 50000, output_dir: str = '/tmp/book_chunks/'):
    """将长文本切分为带偏移标注的数据块"""
    import os, math
    os.makedirs(output_dir, exist_ok=True)
    chunks = []
    num = math.ceil(len(text) / chunk_size)
    for i in range(num):
        start = i * chunk_size
        chunk = text[start:start + chunk_size]
        path = f'{output_dir}chunk_{start}.txt'
        line_offset = text[:start].count('\n')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'--- CHUNK START={start}, LINE_OFFSET={line_offset} ---\n')
            f.write(chunk)
        chunks.append({'offset': start, 'path': path, 'length': len(chunk)})
    return chunks
```
