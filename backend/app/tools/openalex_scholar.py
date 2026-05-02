"""
OpenAlex 学术搜索工具
用于写作手查找参考文献
"""
from typing import Optional
import httpx

from app.schemas.A2A import Citation
from app.utils.log_util import logger


class OpenAlexScholar:
    """OpenAlex 学术搜索引擎"""

    BASE_URL = "https://api.openalex.org/works"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30)

    async def search(
        self, query: str, max_results: int = 5
    ) -> list[Citation]:
        """搜索论文"""
        try:
            params = {
                "search": query,
                "per_page": max_results,
                "sort": "cited_by_count:desc",
            }
            response = await self.client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

            results = []
            for work in data.get("results", []):
                authors = work.get("authorships", [])
                author_names = ", ".join(
                    [
                        a.get("author", {}).get("display_name", "")
                        for a in authors[:3]
                    ]
                )
                if len(authors) > 3:
                    author_names += " et al."

                results.append(
                    Citation(
                        title=work.get("title", ""),
                        authors=author_names,
                        year=str(work.get("publication_year", "")),
                        doi=work.get("doi", ""),
                        url=work.get("doi", f"https://doi.org/{work.get('doi')}")
                        if work.get("doi")
                        else "",
                    )
                )

            logger.info(f"OpenAlex 搜索完成: '{query}' -> {len(results)} 结果")
            return results

        except Exception as e:
            logger.error(f"OpenAlex 搜索失败: {e}")
            return []

    async def search_papers(self, query: str, max_results: int = 5) -> list[Citation]:
        """搜索论文（别名，兼容 writer_agent 调用）"""
        return await self.search(query, max_results)

    def papers_to_str(self, papers: list[Citation]) -> str:
        """将论文列表格式化为可读字符串"""
        if not papers:
            return "未找到相关论文"
        lines = []
        for i, p in enumerate(papers, 1):
            line = f"{i}. {p.title}"
            if p.authors:
                line += f" - {p.authors}"
            if p.year:
                line += f" ({p.year})"
            if p.doi:
                line += f" DOI: {p.doi}"
            lines.append(line)
        return "\n".join(lines)

    async def close(self):
        await self.client.aclose()
