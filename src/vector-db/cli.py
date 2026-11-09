"""
GreenPath RAG 小样例（建索引这一侧）：MD 资料 → 切块 → 转向量 → 存进 ChromaDB → 试着检索。
结构照课程 llm-rag 教程：一个 cli.py，用开关分步跑。回答问题那一侧以后写在 api-service 里，这里不做。

用法（在 docker-shell.sh 打开的容器里，--chunk_type 四步要一致）：
  python cli.py --chunk --chunk_type markdown-split      切块，结果在 outputs/chunks-*.jsonl（不调 API）
  python cli.py --embed --chunk_type markdown-split      每块转成向量（调 Gemini embedding）
  python cli.py --load  --chunk_type markdown-split      写进 ChromaDB
  python cli.py --query --chunk_type markdown-split --question "Can I ride a bike on the sidewalk?"
"""
import os
import re
import glob
import time
import argparse
import hashlib
import yaml
import pandas as pd
import chromadb
from google import genai
from google.genai import types, errors
from langchain_text_splitters import CharacterTextSplitter, RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter

# 设置
GCP_LOCATION = "global"
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSION = 256
INPUT_FOLDER = "input-datasets"
OUTPUT_FOLDER = "outputs"
CHROMADB_HOST = os.environ.get("CHROMADB_HOST", "greenpath-chromadb")
CHROMADB_PORT = 8000

# 每块约 1000 字符，相邻两块重叠 100 字符（免得一句话被切断后两边都不完整）
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100

# MD 开头的 YAML 信息头里，挑这几项存成每一块的 metadata（检索时可以过滤、回答时可以附来源）
META_KEYS = ["source_id", "title", "publisher", "source_url", "region", "question_ids"]

# 按标题切时认哪几级标题
HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4")]

# 比这还短的块丢掉：多半是只有一个标题、正文在下一级标题里（标题已经记在 section 里，不会丢）
MIN_CHUNK = 50


# Gemini client：第一次用到才建，所以 --chunk 不需要 GCP 凭证
_client = None
def llm_client():
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=os.environ["GCP_PROJECT"], location=GCP_LOCATION)
    return _client


# 读一个 MD：拆成 (信息头 dict, 正文)。信息头不当正文切，否则会被检索出来当成内容
def read_md(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    meta = {}
    if text.startswith("---"):
        _, header, text = text.split("---", 2)
        meta = yaml.safe_load(header) or {}
    # ChromaDB 的 metadata 只收字符串/数字：空值跳过，列表拼成 "A01,A02"
    meta = {k: (",".join(map(str, meta[k])) if isinstance(meta[k], list) else meta[k])
            for k in META_KEYS if meta.get(k) is not None}
    # 去掉 PDF 转换留下的 HTML 注释，例如 <!-- PDF page 4 of 5 -->
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return meta, text.strip()


# 把正文切成块，返回 [(所属标题, 块文字), ...]
def split_text(text, method):
    recursive = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    # 对照组：固定字数硬切，不管句子和段落
    if method == "char-split":
        splitter = CharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
                                         separator="", strip_whitespace=False)
        return [("", c) for c in splitter.split_text(text)]

    # 先按段落切，太长再按换行、句子、空格切
    if method == "recursive-split":
        return [("", c) for c in recursive.split_text(text)]

    # 先按 # / ## / ### 标题切成一节一节，每块记下所属标题；某节太长再用 recursive 切
    # 没有标题的文件整篇算一节，效果等于 recursive-split
    if method == "markdown-split":
        sections = MarkdownHeaderTextSplitter(HEADERS, strip_headers=False).split_text(text)
        out = []
        for s in sections:
            section = " > ".join(s.metadata[name].replace("**", "") for _, name in HEADERS if name in s.metadata)
            out += [(section, c) for c in recursive.split_text(s.page_content)]
        return out

    raise ValueError(f"unknown chunk_type: {method}")


# 一批文字 → 向量（Vertex 一次最多 250 条；遇到限流等错误就等一会儿重试）
# task_type：存进库的资料用 RETRIEVAL_DOCUMENT，用户的问题用 RETRIEVAL_QUERY，检索更准
def generate_embeddings(texts, task_type, batch_size=100, max_retries=5, retry_delay=5):
    embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        for attempt in range(max_retries + 1):
            try:
                response = llm_client().models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=batch,
                    config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSION, task_type=task_type),
                )
                embeddings += [e.values for e in response.embeddings]
                break
            except errors.APIError as e:
                if attempt == max_retries:
                    raise
                wait = retry_delay * 2 ** attempt
                print(f"API error ({e.code}): {e.message}. Retrying in {wait}s ...")
                time.sleep(wait)
    return embeddings


def chunk(method):
    print("chunk()")
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    md_files = sorted(glob.glob(os.path.join(INPUT_FOLDER, "*.md")))
    print("Number of files to process:", len(md_files))

    lengths = []
    for md_file in md_files:
        meta, body = read_md(md_file)
        source_id = meta.get("source_id") or os.path.basename(md_file)[:-3]

        # 每块一行：块文字 + 所属标题 + 信息头里挑出的 metadata
        rows = [{"chunk": c, "section": section, **meta}
                for section, c in split_text(body, method) if len(c.strip()) >= MIN_CHUNK]
        df = pd.DataFrame(rows)
        lengths += df["chunk"].str.len().tolist()
        print(f"{source_id}: {len(body):>6} chars -> {len(df):>3} chunks")

        with open(os.path.join(OUTPUT_FOLDER, f"chunks-{method}-{source_id}.jsonl"), "w", encoding="utf-8") as f:
            f.write(df.to_json(orient="records", lines=True, force_ascii=False))

    # 汇总：比较不同切法时看这几个数
    s = pd.Series(lengths)
    print(f"\n[{method}] total chunks: {len(s)} | length avg {s.mean():.0f}, min {s.min()}, max {s.max()}")


def embed(method):
    print("embed()")
    jsonl_files = sorted(glob.glob(os.path.join(OUTPUT_FOLDER, f"chunks-{method}-*.jsonl")))
    print("Number of files to process:", len(jsonl_files))

    for jsonl_file in jsonl_files:
        df = pd.read_json(jsonl_file, lines=True, dtype=False)
        df["embedding"] = generate_embeddings(df["chunk"].tolist(), "RETRIEVAL_DOCUMENT")
        print(f"{os.path.basename(jsonl_file)}: {len(df)} embeddings")

        with open(jsonl_file.replace("chunks-", "embeddings-"), "w", encoding="utf-8") as f:
            f.write(df.to_json(orient="records", lines=True, force_ascii=False))


def load(method):
    print("load()")
    client = chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)

    # 每种切法一个集合，方便比较；每次重建，免得旧数据混进来
    collection_name = f"greenpath-docs-{method}"
    try:
        client.delete_collection(name=collection_name)
        print(f"Deleted existing collection '{collection_name}'")
    except Exception:
        pass
    collection = client.create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})

    jsonl_files = sorted(glob.glob(os.path.join(OUTPUT_FOLDER, f"embeddings-{method}-*.jsonl")))
    total = 0
    for jsonl_file in jsonl_files:
        df = pd.read_json(jsonl_file, lines=True, dtype=False)
        meta_cols = [c for c in df.columns if c not in ("chunk", "embedding")]

        # id = 文件 id 的哈希 + 块序号，保证不重复
        prefix = hashlib.sha256(df["source_id"][0].encode()).hexdigest()[:16]
        collection.add(
            ids=[f"{prefix}-{i}" for i in range(len(df))],
            documents=df["chunk"].tolist(),
            metadatas=df[meta_cols].fillna("").to_dict(orient="records"),
            embeddings=df["embedding"].tolist(),
        )
        total += len(df)
    print(f"Inserted {total} chunks into '{collection_name}'")


def query(method, question, n_results=5):
    print("query()")
    client = chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)
    collection = client.get_collection(name=f"greenpath-docs-{method}")

    # 问题也转成向量，找距离最近的几块（cosine 距离越小越相近）
    query_embedding = generate_embeddings([question], "RETRIEVAL_QUERY")[0]
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        # where={"source_id": "GP-401"},          # 例：只在某一份资料里找
        # where_document={"$contains": "helmet"}, # 例：块里必须包含某个词
    )

    print("Question:", question)
    for rank, (doc, meta, dist) in enumerate(zip(results["documents"][0], results["metadatas"][0], results["distances"][0]), 1):
        print(f"\n#{rank}  distance={dist:.3f}  {meta.get('source_id')}  {meta.get('title')}  [{meta.get('section')}]")
        print(doc[:300].replace("\n", " "), "...")


def main(args=None):
    print("CLI Arguments:", args)

    if args.chunk:
        chunk(args.chunk_type)

    if args.embed:
        embed(args.chunk_type)

    if args.load:
        load(args.chunk_type)

    if args.query:
        query(args.chunk_type, args.question)


if __name__ == "__main__":
    # 运行 python cli.py --help 可以看到下面这些开关
    parser = argparse.ArgumentParser(description="GreenPath vector-db CLI")

    parser.add_argument("--chunk", action="store_true", help="切块")
    parser.add_argument("--embed", action="store_true", help="转向量")
    parser.add_argument("--load", action="store_true", help="写进 ChromaDB")
    parser.add_argument("--query", action="store_true", help="检索")
    parser.add_argument("--chunk_type", default="markdown-split",
                        choices=["char-split", "recursive-split", "markdown-split"])
    parser.add_argument("--question", default="Can I ride a bike on the sidewalk in Massachusetts?")

    args = parser.parse_args()

    main(args)
