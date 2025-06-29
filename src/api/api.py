import asyncio
import time
from contextlib import asynccontextmanager
import httpx
from typing import Dict
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import random

app = FastAPI()
leetcode_url = "https://leetcode.com/graphql"
client = httpx.AsyncClient()

class QuestionCache:
    def __init__(self):
        self.questions: Dict[str, dict] = {}
        self.slug_to_id: Dict[str, str] = {}
        self.frontend_id_to_slug: Dict[str, str] = {}
        self.question_details: Dict[str, dict] = {}
        self.last_updated: float = 0
        self.update_interval: int = 3600
        self.lock = asyncio.Lock()

    async def initialize(self):
        async with self.lock:
            if not self.questions or (time.time() - self.last_updated) > self.update_interval:
                await self._fetch_all_questions()
                self.last_updated = time.time()

    async def _fetch_all_questions(self):
        query = """query problemsetQuestionList {
            problemsetQuestionList: questionList(
                categorySlug: ""
                limit: 10000
                skip: 0
                filters: {}
            ) {
                questions: data {
                    questionId
                    questionFrontendId
                    title
                    titleSlug                    
                    difficulty                    
                    paidOnly: isPaidOnly      
                    hasSolution
                    hasVideoSolution                                                           
                }
            }
        }"""
        
        try:
            response = await client.post(leetcode_url, json={"query": query})
            if response.status_code == 200:
                data = response.json()
                questions = data["data"]["problemsetQuestionList"]["questions"]
                
                self.questions.clear()
                self.slug_to_id.clear()
                self.frontend_id_to_slug.clear()
                
                for q in questions:
                    self.questions[q["questionId"]] = q
                    self.slug_to_id[q["titleSlug"]] = q["questionId"]
                    self.frontend_id_to_slug[q["questionFrontendId"]] = q["titleSlug"]                    
        except Exception as e:
            print(f"Error updating questions: {e}")

cache = QuestionCache()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await cache.initialize()
    yield

app = FastAPI(lifespan=lifespan)

async def fetch_with_retry(url: str, payload: dict, retries: int = 3):
    for _ in range(retries):
        try:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"Request failed: {e}")
            await asyncio.sleep(1)
    return None

@app.get("/problems", tags=["Problems"])
async def get_all_problems():
    await cache.initialize()
    return [{
        "id": q["questionId"],
        "frontend_id": q["questionFrontendId"],
        "title": q["title"],
        "title_slug": q["titleSlug"],
        "url": f"https://leetcode.com/problems/{q['titleSlug']}/",
        "difficulty": q["difficulty"],                
        "paid_only": q["paidOnly"],        
        "has_solution": q["hasSolution"],
        "has_video_solution": q["hasVideoSolution"],        
    } for q in cache.questions.values()]

@app.get("/problem/{id_or_slug}", tags=["Problems"])
async def get_problem(id_or_slug: str):
    await cache.initialize()
    
    if id_or_slug in cache.frontend_id_to_slug:
        slug = cache.frontend_id_to_slug[id_or_slug]
    elif id_or_slug in cache.slug_to_id:
        slug = id_or_slug
    else:
        raise HTTPException(status_code=404, detail="Question not found")

    # check cache
    question_id = cache.slug_to_id[slug]
    if question_id in cache.question_details:
        return cache.question_details[question_id]

    # not in cache, fetch from leetcode
    query = """query questionData($titleSlug: String!) {
        question(titleSlug: $titleSlug) {            
            questionId
            questionFrontendId
            title
            content
            likes
            dislikes
            stats
            similarQuestions
            categoryTitle
            hints
            topicTags { name }
            companyTags { name }
            difficulty
            isPaidOnly
            solution { canSeeDetail content }
            hasSolution 
            hasVideoSolution
        }
    }"""
    
    payload = {
        "query": query,
        "variables": {"titleSlug": slug}
    }
    
    data = await fetch_with_retry(leetcode_url, payload)
    if not data or "data" not in data or not data["data"]["question"]:
        raise HTTPException(status_code=404, detail="Question data not found")
    
    question_data = data["data"]["question"]
    question_data["url"] = f"https://leetcode.com/problems/{slug}/"
        
    cache.question_details[question_id] = question_data
    return question_data

@app.get("/search", tags=["Problems"])
async def search_problems(query: str):
    """
    Search for problems whose titles contain the given query (case-insensitive).
    """
    await cache.initialize()
    query_lower = query.lower()
    results = []
    for q in cache.questions.values():
        if query_lower in q["title"].lower():
            results.append({
                "id": q["questionId"],
                "frontend_id": q["questionFrontendId"],
                "title": q["title"],
                "title_slug": q["titleSlug"],
                "url": f"https://leetcode.com/problems/{q['titleSlug']}/"
            })
    return results

@app.get("/random", tags=["Problems"])
async def get_random_problem():
    """
    Return a random problem from the cached questions.
    """
    await cache.initialize()
    if not cache.questions:
        raise HTTPException(status_code=404, detail="No questions available")
    q = random.choice(list(cache.questions.values()))
    return {
        "id": q["questionId"],
        "frontend_id": q["questionFrontendId"],
        "title": q["title"],
        "title_slug": q["titleSlug"],
        "url": f"https://leetcode.com/problems/{q['titleSlug']}/"
    }

@app.get("/user/{username}", tags=["Users"])
async def get_user_profile(username: str):
    async with httpx.AsyncClient() as client:
        query = """query userPublicProfile($username: String!) {
            matchedUser(username: $username) {
                username
                profile {
                    realName
                    websites
                    countryName
                    company
                    school
                    aboutMe
                    reputation
                    ranking
                }
                submitStats {
                    acSubmissionNum {
                        difficulty
                        count
                        submissions
                    }
                    totalSubmissionNum {
                        difficulty
                        count
                        submissions
                    }
                }
            }
        }"""
        
        payload = {
            "query": query,
            "variables": {"username": username},
            "operationName": "userPublicProfile"
        }
        
        try:
            response = await client.post(leetcode_url, json=payload)
            if response.status_code == 200:
                data = response.json()
                if not data.get("data", {}).get("matchedUser"):
                    raise HTTPException(status_code=404, detail="User not found")
                return data["data"]["matchedUser"]
            raise HTTPException(status_code=response.status_code, detail="Error fetching user profile")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/{username}/contests", tags=["Users"])
async def get_user_contest_history(username: str):
    async with httpx.AsyncClient() as client:
        query = """query userContestRankingInfo($username: String!) {
            userContestRanking(username: $username) {
                attendedContestsCount
                rating
                globalRanking
                totalParticipants
                topPercentage
            }
            userContestRankingHistory(username: $username) {
                attended
                trendDirection
                problemsSolved
                totalProblems
                finishTimeInSeconds
                rating
                ranking
            }
        }"""
        
        payload = {
            "query": query,
            "variables": {"username": username},
            "operationName": "userContestRankingInfo"
        }
        
        try:
            response = await client.post(leetcode_url, json=payload)
            if response.status_code == 200:
                data = response.json()
                if not data.get("data"):
                    raise HTTPException(status_code=404, detail="User not found")
                return data["data"]
            raise HTTPException(status_code=response.status_code, detail="Error fetching contest history")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/{username}/submissions", tags=["Users"])
async def get_recent_submissions(username: str, limit: int = 20):
    async with httpx.AsyncClient() as client:
        query = """query recentSubmissions($username: String!, $limit: Int) {
            recentSubmissionList(username: $username, limit: $limit) {
                title
                titleSlug
                timestamp
                statusDisplay
                lang
                url
            }
        }"""
        
        payload = {
            "query": query,
            "variables": {"username": username, "limit": limit}
        }
        
        try:
            response = await client.post(leetcode_url, json=payload)
            if response.status_code == 200:
                data = response.json()
                if "errors" in data:
                    raise HTTPException(status_code=404, detail="User not found")
                return data["data"]["recentSubmissionList"]
            raise HTTPException(status_code=response.status_code, detail="Error fetching submissions")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/daily", tags=["Daily Challenge"])
async def get_daily_challenge():
    async with httpx.AsyncClient() as client:
        query = """query questionOfToday {
            activeDailyCodingChallengeQuestion {
                date
                link
                question {
                    questionId
                    questionFrontendId
                    title
                    titleSlug
                    difficulty
                    content
                }
            }
        }"""
        
        payload = {"query": query}
        
        try:
            response = await client.post(leetcode_url, json=payload)
            if response.status_code == 200:
                data = response.json()
                return data["data"]["activeDailyCodingChallengeQuestion"]
            raise HTTPException(status_code=response.status_code, detail="Error fetching daily challenge")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/problem/{problem_slug}/solutions", tags=["Solutions"])
async def get_solution_articles(
    problem_slug: str, 
    order_by: str = "HOT", 
    skip: int = 0, 
    first: int = 15,
    user_input: str = "",
    tag_slugs: list[str] = None
):
    """
    Get solution articles for a specific problem.
    
    Args:
        problem_slug: The problem's title slug (e.g., "find-subsequence-of-length-k-with-the-largest-sum")
        order_by: Sort order - "HOT", "NEWEST", "OLDEST" (default: "HOT")
        skip: Number of articles to skip (default: 0)
        first: Number of articles to return (default: 15)
        user_input: Search filter for articles (default: "")
        tag_slugs: List of tag slugs to filter by (default: None)
    """
    if tag_slugs is None:
        tag_slugs = []
    
    query = """
    query ugcArticleSolutionArticles($questionSlug: String!, $orderBy: ArticleOrderByEnum, $userInput: String, $tagSlugs: [String!], $skip: Int, $before: String, $after: String, $first: Int, $last: Int, $isMine: Boolean) {
        ugcArticleSolutionArticles(
            questionSlug: $questionSlug
            orderBy: $orderBy
            userInput: $userInput
            tagSlugs: $tagSlugs
            skip: $skip
            first: $first
            before: $before
            after: $after
            last: $last
            isMine: $isMine
        ) {
            totalNum
            pageInfo {
                hasNextPage
            }
            edges {
                node {
                    ...ugcSolutionArticleFragment
                }
            }
        }
    }
    
    fragment ugcSolutionArticleFragment on SolutionArticleNode {
        uuid
        title
        slug
        summary
        author {
            realName
            userAvatar
            userSlug
            userName
            nameColor
            certificationLevel
            activeBadge {
                icon
                displayName
            }
        }
        articleType
        thumbnail
        summary
        createdAt
        updatedAt
        status
        isLeetcode
        canSee
        canEdit
        isMyFavorite
        chargeType
        myReactionType
        topicId
        hitCount
        hasVideoArticle
        reactions {
            count
            reactionType
        }
        title
        slug
        tags {
            name
            slug
            tagType
        }
        topic {
            id
            topLevelCommentCount
        }
    }
    """
    
    payload = {
        "query": query,
        "variables": {
            "questionSlug": problem_slug,
            "orderBy": order_by,
            "userInput": user_input,
            "tagSlugs": tag_slugs,
            "skip": skip,
            "first": first,
            "before": None,
            "after": None,
            "last": None,
            "isMine": False
        },
        "operationName": "ugcArticleSolutionArticles"
    }
    
    try:
        data = await fetch_with_retry(leetcode_url, payload)
        if not data or "data" not in data:
            raise HTTPException(status_code=404, detail="Solution articles not found")
        
        return data["data"]["ugcArticleSolutionArticles"]
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching solution articles: {str(e)}")


@app.get("/problem/{problem_slug}/solutions/search", tags=["Solutions"])
async def search_solution_articles(
    problem_slug: str,
    search_query: str,
    order_by: str = "HOT",
    skip: int = 0,
    first: int = 15
):
    """
    Search solution articles for a specific problem.
    """
    return await get_solution_articles(
        problem_slug=problem_slug,
        order_by=order_by,
        skip=skip,
        first=first,
        user_input=search_query,
        tag_slugs=[]
    )

@app.get("/solution/{solution_id}", tags=["Solutions"])
async def get_solution_content(solution_id: str, id_type: str = "topic"):
    """
    Get the full content of a specific solution article.
    
    Args:
        solution_id: The solution identifier (topic ID or article ID)
        id_type: Type of ID - "topic" for topicId or "article" for articleId (default: "topic")
    
    Returns:
        Full solution article with content, navigation links, and metadata
    """
    
    query = """
    query ugcArticleSolutionArticle($articleId: ID, $topicId: ID) {
        ugcArticleSolutionArticle(articleId: $articleId, topicId: $topicId) {
            ...ugcSolutionArticleFragment
            content
            isSerialized
            isAuthorArticleReviewer
            scoreInfo {
                scoreCoefficient
            }
            prev {
                uuid
                slug
                topicId
                title
            }
            next {
                uuid
                slug
                topicId
                title
            }
        }
    }
    
    fragment ugcSolutionArticleFragment on SolutionArticleNode {
        uuid
        title
        slug
        summary
        author {
            realName
            userAvatar
            userSlug
            userName
            nameColor
            certificationLevel
            activeBadge {
                icon
                displayName
            }
        }
        articleType
        thumbnail
        summary
        createdAt
        updatedAt
        status
        isLeetcode
        canSee
        canEdit
        isMyFavorite
        chargeType
        myReactionType
        topicId
        hitCount
        hasVideoArticle
        reactions {
            count
            reactionType
        }
        title
        slug
        tags {
            name
            slug
            tagType
        }
        topic {
            id
            topLevelCommentCount
        }
    }
    """
    
    # Set up variables based on ID type
    variables = {}
    if id_type.lower() == "topic":
        variables["topicId"] = solution_id
        variables["articleId"] = None
    elif id_type.lower() == "article":
        variables["articleId"] = solution_id
        variables["topicId"] = None
    else:
        raise HTTPException(status_code=400, detail="id_type must be 'topic' or 'article'")
    
    payload = {
        "query": query,
        "variables": variables,
        "operationName": "ugcArticleSolutionArticle"
    }
    
    try:
        data = await fetch_with_retry(leetcode_url, payload)
        if not data or "data" not in data or not data["data"]["ugcArticleSolutionArticle"]:
            raise HTTPException(status_code=404, detail="Solution article not found")
        
        return data["data"]["ugcArticleSolutionArticle"]
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching solution content: {str(e)}")


@app.get("/solution/topic/{topic_id}", tags=["Solutions"])
async def get_solution_by_topic_id(topic_id: str):
    """
    Get solution content by topic ID (convenience endpoint).
    
    Args:
        topic_id: The topic ID of the solution
    """
    return await get_solution_content(topic_id, "topic")


@app.get("/solution/article/{article_id}", tags=["Solutions"])
async def get_solution_by_article_id(article_id: str):
    """
    Get solution content by article ID (convenience endpoint).
    
    Args:
        article_id: The article ID of the solution
    """
    return await get_solution_content(article_id, "article")

@app.get("/health", tags=["Utility"])
async def health_check():
    return {"status": "ok", "timestamp": time.time()}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home():
    from src.api.home import HOME_PAGE_HTML
    return HOME_PAGE_HTML 

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8081/"], # add prod frontend later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
