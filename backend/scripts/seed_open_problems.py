"""Seed the Problem bank with ~20 classic algorithm exercises spanning easy,
medium, and hard difficulty. These are original write-ups of well-known,
public-domain algorithmic concepts (two-sum, palindromes, binary search,
dynamic programming, graph cycles, etc.) — not copied from any single site.
Each problem ships with at least 5 test cases, matching the platform's
minimum-test-case requirement for authored problems.

Idempotent: skips any slug that already exists.

Run:  docker compose exec backend python -m scripts.seed_open_problems
"""

from app.academy.models import Problem, TestCase
from app.core.database import SessionLocal

PROBLEMS = [
    # ---------------- easy ----------------
    {
        "slug": "sum-two-numbers",
        "title": "Sum of Two Numbers",
        "difficulty": "easy",
        "description": (
            "Read two space-separated integers `a` and `b` from stdin and print "
            "their sum.\n\nExample\nInput: 2 3\nOutput: 5"
        ),
        "starter_code": "a, b = map(int, input().split())\n# your code here\n",
        "reference_solution": "a, b = map(int, input().split())\nprint(a + b)\n",
        "test_cases": [
            {"stdin": "2 3", "expected_output": "5", "is_sample": True},
            {"stdin": "-4 10", "expected_output": "6", "is_sample": False},
            {"stdin": "0 0", "expected_output": "0", "is_sample": False},
            {"stdin": "100 200", "expected_output": "300", "is_sample": False},
            {"stdin": "1000000 1", "expected_output": "1000001", "is_sample": False},
        ],
    },
    {
        "slug": "reverse-string",
        "title": "Reverse a String",
        "difficulty": "easy",
        "description": (
            "Read a single line of text and print it reversed.\n\n"
            "Example\nInput: hello\nOutput: olleh"
        ),
        "starter_code": "s = input()\n# your code here\n",
        "reference_solution": "s = input()\nprint(s[::-1])\n",
        "test_cases": [
            {"stdin": "hello", "expected_output": "olleh", "is_sample": True},
            {"stdin": "Academy", "expected_output": "ymedacA", "is_sample": False},
            {"stdin": "a", "expected_output": "a", "is_sample": False},
            {"stdin": "racecar", "expected_output": "racecar", "is_sample": False},
            {"stdin": "Python3", "expected_output": "3nohtyP", "is_sample": False},
        ],
    },
    {
        "slug": "is-palindrome",
        "title": "Palindrome Check",
        "difficulty": "easy",
        "description": (
            "Read a single word (lowercase letters only) and print `yes` if it "
            "reads the same forwards and backwards, otherwise print `no`.\n\n"
            "Example\nInput: level\nOutput: yes"
        ),
        "starter_code": "s = input()\n# your code here\n",
        "reference_solution": "s = input()\nprint('yes' if s == s[::-1] else 'no')\n",
        "test_cases": [
            {"stdin": "level", "expected_output": "yes", "is_sample": True},
            {"stdin": "hello", "expected_output": "no", "is_sample": False},
            {"stdin": "a", "expected_output": "yes", "is_sample": False},
            {"stdin": "noon", "expected_output": "yes", "is_sample": False},
            {"stdin": "python", "expected_output": "no", "is_sample": False},
        ],
    },
    {
        "slug": "fizzbuzz",
        "title": "FizzBuzz",
        "difficulty": "easy",
        "description": (
            "Read an integer `n`. For each integer `i` from 1 to n (inclusive), "
            "print `Fizz` if i is divisible by 3, `Buzz` if divisible by 5, "
            "`FizzBuzz` if divisible by both, otherwise print `i`. One value per "
            "line.\n\nExample\nInput: 5\nOutput:\n1\n2\nFizz\n4\nBuzz"
        ),
        "starter_code": "n = int(input())\n# your code here\n",
        "reference_solution": (
            "n = int(input())\n"
            "for i in range(1, n + 1):\n"
            "    if i % 15 == 0:\n"
            "        print('FizzBuzz')\n"
            "    elif i % 3 == 0:\n"
            "        print('Fizz')\n"
            "    elif i % 5 == 0:\n"
            "        print('Buzz')\n"
            "    else:\n"
            "        print(i)\n"
        ),
        "test_cases": [
            {"stdin": "5", "expected_output": "1\n2\nFizz\n4\nBuzz", "is_sample": True},
            {
                "stdin": "15",
                "expected_output": "1\n2\nFizz\n4\nBuzz\nFizz\n7\n8\nFizz\nBuzz\n11\nFizz\n13\n14\nFizzBuzz",
                "is_sample": False,
            },
            {"stdin": "1", "expected_output": "1", "is_sample": False},
            {"stdin": "3", "expected_output": "1\n2\nFizz", "is_sample": False},
            {
                "stdin": "20",
                "expected_output": (
                    "1\n2\nFizz\n4\nBuzz\nFizz\n7\n8\nFizz\nBuzz\n11\nFizz\n13\n14\n"
                    "FizzBuzz\n16\n17\nFizz\n19\nBuzz"
                ),
                "is_sample": False,
            },
        ],
    },
    {
        "slug": "count-vowels",
        "title": "Count Vowels",
        "difficulty": "easy",
        "description": (
            "Read a line of lowercase text and print how many of its characters "
            "are vowels (a, e, i, o, u).\n\nExample\nInput: hello world\nOutput: 3"
        ),
        "starter_code": "s = input()\n# your code here\n",
        "reference_solution": "s = input()\nprint(sum(1 for c in s if c in 'aeiou'))\n",
        "test_cases": [
            {"stdin": "hello world", "expected_output": "3", "is_sample": True},
            {"stdin": "sky", "expected_output": "0", "is_sample": False},
            {"stdin": "aeiou", "expected_output": "5", "is_sample": False},
            {"stdin": "xyz", "expected_output": "0", "is_sample": False},
            {"stdin": "programming", "expected_output": "3", "is_sample": False},
        ],
    },
    {
        "slug": "max-of-three",
        "title": "Largest of Three",
        "difficulty": "easy",
        "description": (
            "Read three space-separated integers and print the largest one.\n\n"
            "Example\nInput: 4 9 2\nOutput: 9"
        ),
        "starter_code": "a, b, c = map(int, input().split())\n# your code here\n",
        "reference_solution": "a, b, c = map(int, input().split())\nprint(max(a, b, c))\n",
        "test_cases": [
            {"stdin": "4 9 2", "expected_output": "9", "is_sample": True},
            {"stdin": "-1 -5 -3", "expected_output": "-1", "is_sample": False},
            {"stdin": "7 7 7", "expected_output": "7", "is_sample": False},
            {"stdin": "0 0 0", "expected_output": "0", "is_sample": False},
            {"stdin": "100 50 75", "expected_output": "100", "is_sample": False},
        ],
    },
    {
        "slug": "factorial",
        "title": "Factorial",
        "difficulty": "easy",
        "description": (
            "Read a non-negative integer `n` and print n! (n factorial). "
            "0! is defined as 1.\n\nExample\nInput: 5\nOutput: 120"
        ),
        "starter_code": "n = int(input())\n# your code here\n",
        "reference_solution": (
            "n = int(input())\n"
            "result = 1\n"
            "for i in range(2, n + 1):\n"
            "    result *= i\n"
            "print(result)\n"
        ),
        "test_cases": [
            {"stdin": "5", "expected_output": "120", "is_sample": True},
            {"stdin": "0", "expected_output": "1", "is_sample": False},
            {"stdin": "10", "expected_output": "3628800", "is_sample": False},
            {"stdin": "1", "expected_output": "1", "is_sample": False},
            {"stdin": "3", "expected_output": "6", "is_sample": False},
        ],
    },
    # ---------------- medium ----------------
    {
        "slug": "two-sum-indices",
        "title": "Two Sum — Indices",
        "difficulty": "medium",
        "description": (
            "Read an integer `target`, then a line of space-separated integers "
            "(the array). Print the 0-based indices `i j` (i < j) of the two "
            "elements that add up to `target`. Assume exactly one solution "
            "exists.\n\nExample\nInput:\n9\n2 7 11 15\nOutput: 0 1"
        ),
        "starter_code": "target = int(input())\nnums = list(map(int, input().split()))\n# your code here\n",
        "reference_solution": (
            "target = int(input())\n"
            "nums = list(map(int, input().split()))\n"
            "seen = {}\n"
            "for i, v in enumerate(nums):\n"
            "    need = target - v\n"
            "    if need in seen:\n"
            "        print(seen[need], i)\n"
            "        break\n"
            "    seen[v] = i\n"
        ),
        "test_cases": [
            {"stdin": "9\n2 7 11 15", "expected_output": "0 1", "is_sample": True},
            {"stdin": "6\n3 2 4", "expected_output": "1 2", "is_sample": False},
            {"stdin": "8\n3 3 2", "expected_output": "0 1", "is_sample": False},
            {"stdin": "6\n1 5 3", "expected_output": "0 1", "is_sample": False},
            {"stdin": "10\n1 2 3 4 6", "expected_output": "3 4", "is_sample": False},
        ],
    },
    {
        "slug": "valid-parentheses",
        "title": "Balanced Brackets",
        "difficulty": "medium",
        "description": (
            "Read a line containing only the characters `()[]{}`. Print `yes` if "
            "every opening bracket has a matching closing bracket in the correct "
            "order, otherwise print `no`.\n\nExample\nInput: ([]){}\nOutput: yes"
        ),
        "starter_code": "s = input()\n# your code here\n",
        "reference_solution": (
            "s = input()\n"
            "pairs = {')': '(', ']': '[', '}': '{'}\n"
            "stack = []\n"
            "ok = True\n"
            "for c in s:\n"
            "    if c in '([{':\n"
            "        stack.append(c)\n"
            "    else:\n"
            "        if not stack or stack.pop() != pairs[c]:\n"
            "            ok = False\n"
            "            break\n"
            "if stack:\n"
            "    ok = False\n"
            "print('yes' if ok else 'no')\n"
        ),
        "test_cases": [
            {"stdin": "([]){}", "expected_output": "yes", "is_sample": True},
            {"stdin": "([)]", "expected_output": "no", "is_sample": False},
            {"stdin": "(((", "expected_output": "no", "is_sample": False},
            {"stdin": "{[()]}", "expected_output": "yes", "is_sample": False},
            {"stdin": "]", "expected_output": "no", "is_sample": False},
        ],
    },
    {
        "slug": "anagram-check",
        "title": "Anagram Check",
        "difficulty": "medium",
        "description": (
            "Read two lowercase words on separate lines. Print `yes` if they are "
            "anagrams of each other (same letters, same counts, any order), "
            "otherwise print `no`.\n\nExample\nInput:\nlisten\nsilent\nOutput: yes"
        ),
        "starter_code": "a = input()\nb = input()\n# your code here\n",
        "reference_solution": "a = input()\nb = input()\nprint('yes' if sorted(a) == sorted(b) else 'no')\n",
        "test_cases": [
            {"stdin": "listen\nsilent", "expected_output": "yes", "is_sample": True},
            {"stdin": "hello\nworld", "expected_output": "no", "is_sample": False},
            {"stdin": "aab\naba", "expected_output": "yes", "is_sample": False},
            {"stdin": "abc\ncba", "expected_output": "yes", "is_sample": False},
            {"stdin": "abc\nabd", "expected_output": "no", "is_sample": False},
        ],
    },
    {
        "slug": "binary-search",
        "title": "Binary Search",
        "difficulty": "medium",
        "description": (
            "Read an integer `target`, then a line of space-separated integers "
            "sorted in ascending order. Print the 0-based index of `target` in "
            "the array, or `-1` if it isn't present. Solve it in O(log n).\n\n"
            "Example\nInput:\n5\n1 3 5 7 9\nOutput: 2"
        ),
        "starter_code": "target = int(input())\nnums = list(map(int, input().split()))\n# your code here\n",
        "reference_solution": (
            "target = int(input())\n"
            "nums = list(map(int, input().split()))\n"
            "lo, hi = 0, len(nums) - 1\n"
            "result = -1\n"
            "while lo <= hi:\n"
            "    mid = (lo + hi) // 2\n"
            "    if nums[mid] == target:\n"
            "        result = mid\n"
            "        break\n"
            "    elif nums[mid] < target:\n"
            "        lo = mid + 1\n"
            "    else:\n"
            "        hi = mid - 1\n"
            "print(result)\n"
        ),
        "test_cases": [
            {"stdin": "5\n1 3 5 7 9", "expected_output": "2", "is_sample": True},
            {"stdin": "2\n1 3 5 7 9", "expected_output": "-1", "is_sample": False},
            {"stdin": "1\n1", "expected_output": "0", "is_sample": False},
            {
                "stdin": "7\n1 2 3 4 5 6 7 8 9",
                "expected_output": "6",
                "is_sample": False,
            },
            {"stdin": "1\n2 3 4", "expected_output": "-1", "is_sample": False},
        ],
    },
    {
        "slug": "merge-sorted-arrays",
        "title": "Merge Two Sorted Arrays",
        "difficulty": "medium",
        "description": (
            "Read two lines, each a space-separated list of integers already "
            "sorted ascending. Print the two lists merged into one sorted, "
            "space-separated list.\n\nExample\nInput:\n1 3 5\n2 4 6\n"
            "Output: 1 2 3 4 5 6"
        ),
        "starter_code": "a = list(map(int, input().split()))\nb = list(map(int, input().split()))\n# your code here\n",
        "reference_solution": (
            "a = list(map(int, input().split()))\n"
            "b = list(map(int, input().split()))\n"
            "i = j = 0\n"
            "merged = []\n"
            "while i < len(a) and j < len(b):\n"
            "    if a[i] <= b[j]:\n"
            "        merged.append(a[i]); i += 1\n"
            "    else:\n"
            "        merged.append(b[j]); j += 1\n"
            "merged.extend(a[i:])\n"
            "merged.extend(b[j:])\n"
            "print(' '.join(map(str, merged)))\n"
        ),
        "test_cases": [
            {
                "stdin": "1 3 5\n2 4 6",
                "expected_output": "1 2 3 4 5 6",
                "is_sample": True,
            },
            {
                "stdin": "1 2 3\n4 5 6",
                "expected_output": "1 2 3 4 5 6",
                "is_sample": False,
            },
            {"stdin": "\n1", "expected_output": "1", "is_sample": False},
            {
                "stdin": "1 5 9\n2 3 4",
                "expected_output": "1 2 3 4 5 9",
                "is_sample": False,
            },
            {"stdin": "10\n1 2 3", "expected_output": "1 2 3 10", "is_sample": False},
        ],
    },
    {
        "slug": "longest-word",
        "title": "Longest Word in a Sentence",
        "difficulty": "medium",
        "description": (
            "Read a line of space-separated words and print the longest one. If "
            "there's a tie, print the one that appears first.\n\n"
            "Example\nInput: the quick brown fox\nOutput: quick"
        ),
        "starter_code": "words = input().split()\n# your code here\n",
        "reference_solution": (
            "words = input().split()\n"
            "best = words[0]\n"
            "for w in words[1:]:\n"
            "    if len(w) > len(best):\n"
            "        best = w\n"
            "print(best)\n"
        ),
        "test_cases": [
            {
                "stdin": "the quick brown fox",
                "expected_output": "quick",
                "is_sample": True,
            },
            {"stdin": "a bb ccc", "expected_output": "ccc", "is_sample": False},
            {"stdin": "cat dog", "expected_output": "cat", "is_sample": False},
            {"stdin": "aa bb cc dd", "expected_output": "aa", "is_sample": False},
            {
                "stdin": "one two three four",
                "expected_output": "three",
                "is_sample": False,
            },
        ],
    },
    {
        "slug": "matrix-transpose",
        "title": "Matrix Transpose",
        "difficulty": "medium",
        "description": (
            "Read integers `rows` and `cols`, then `rows` lines each containing "
            "`cols` space-separated integers. Print the transposed matrix: "
            "`cols` lines each with `rows` space-separated integers.\n\n"
            "Example\nInput:\n2 3\n1 2 3\n4 5 6\nOutput:\n1 4\n2 5\n3 6"
        ),
        "starter_code": (
            "rows, cols = map(int, input().split())\n"
            "matrix = [list(map(int, input().split())) for _ in range(rows)]\n"
            "# your code here\n"
        ),
        "reference_solution": (
            "rows, cols = map(int, input().split())\n"
            "matrix = [list(map(int, input().split())) for _ in range(rows)]\n"
            "for c in range(cols):\n"
            "    print(' '.join(str(matrix[r][c]) for r in range(rows)))\n"
        ),
        "test_cases": [
            {
                "stdin": "2 3\n1 2 3\n4 5 6",
                "expected_output": "1 4\n2 5\n3 6",
                "is_sample": True,
            },
            {"stdin": "1 1\n7", "expected_output": "7", "is_sample": False},
            {
                "stdin": "3 2\n1 2\n3 4\n5 6",
                "expected_output": "1 3 5\n2 4 6",
                "is_sample": False,
            },
            {"stdin": "1 3\n1 2 3", "expected_output": "1\n2\n3", "is_sample": False},
            {"stdin": "3 1\n1\n2\n3", "expected_output": "1 2 3", "is_sample": False},
        ],
    },
    # ---------------- hard ----------------
    {
        "slug": "longest-palindromic-substring",
        "title": "Longest Palindromic Substring",
        "difficulty": "hard",
        "description": (
            "Read a lowercase string `s` and print its longest palindromic "
            "substring. If there are multiple of the same maximum length, print "
            "the one starting at the smallest index.\n\n"
            "Example\nInput: babad\nOutput: bab"
        ),
        "starter_code": "s = input()\n# your code here\n",
        "reference_solution": (
            "s = input()\n"
            "def expand(l, r):\n"
            "    while l >= 0 and r < len(s) and s[l] == s[r]:\n"
            "        l -= 1; r += 1\n"
            "    return s[l + 1:r]\n"
            "best = ''\n"
            "for i in range(len(s)):\n"
            "    for cand in (expand(i, i), expand(i, i + 1)):\n"
            "        if len(cand) > len(best):\n"
            "            best = cand\n"
            "print(best)\n"
        ),
        "test_cases": [
            {"stdin": "babad", "expected_output": "bab", "is_sample": True},
            {"stdin": "cbbd", "expected_output": "bb", "is_sample": False},
            {"stdin": "a", "expected_output": "a", "is_sample": False},
            {
                "stdin": "forgeeksskeegfor",
                "expected_output": "geeksskeeg",
                "is_sample": False,
            },
            {"stdin": "abc", "expected_output": "a", "is_sample": False},
        ],
    },
    {
        "slug": "coin-change-min-count",
        "title": "Coin Change — Minimum Coins",
        "difficulty": "hard",
        "description": (
            "Read an integer `amount`, then a line of space-separated integer "
            "coin denominations (unlimited supply of each). Print the minimum "
            "number of coins needed to make exactly `amount`, or `-1` if it "
            "can't be done.\n\nExample\nInput:\n11\n1 2 5\nOutput: 3"
        ),
        "starter_code": "amount = int(input())\ncoins = list(map(int, input().split()))\n# your code here\n",
        "reference_solution": (
            "amount = int(input())\n"
            "coins = list(map(int, input().split()))\n"
            "INF = float('inf')\n"
            "dp = [0] + [INF] * amount\n"
            "for a in range(1, amount + 1):\n"
            "    for c in coins:\n"
            "        if c <= a and dp[a - c] + 1 < dp[a]:\n"
            "            dp[a] = dp[a - c] + 1\n"
            "print(dp[amount] if dp[amount] != INF else -1)\n"
        ),
        "test_cases": [
            {"stdin": "11\n1 2 5", "expected_output": "3", "is_sample": True},
            {"stdin": "3\n2", "expected_output": "-1", "is_sample": False},
            {"stdin": "0\n1 2 5", "expected_output": "0", "is_sample": False},
            {"stdin": "7\n1 3 4", "expected_output": "2", "is_sample": False},
            {"stdin": "1\n2 5", "expected_output": "-1", "is_sample": False},
        ],
    },
    {
        "slug": "longest-increasing-subsequence",
        "title": "Longest Increasing Subsequence",
        "difficulty": "hard",
        "description": (
            "Read a line of space-separated integers and print the length of "
            "the longest strictly increasing subsequence (elements need not be "
            "contiguous).\n\nExample\nInput: 10 9 2 5 3 7 101 18\nOutput: 4"
        ),
        "starter_code": "nums = list(map(int, input().split()))\n# your code here\n",
        "reference_solution": (
            "nums = list(map(int, input().split()))\n"
            "if not nums:\n"
            "    print(0)\n"
            "else:\n"
            "    dp = [1] * len(nums)\n"
            "    for i in range(len(nums)):\n"
            "        for j in range(i):\n"
            "            if nums[j] < nums[i]:\n"
            "                dp[i] = max(dp[i], dp[j] + 1)\n"
            "    print(max(dp))\n"
        ),
        "test_cases": [
            {"stdin": "10 9 2 5 3 7 101 18", "expected_output": "4", "is_sample": True},
            {"stdin": "7 7 7 7", "expected_output": "1", "is_sample": False},
            {"stdin": "1 2 3 4", "expected_output": "4", "is_sample": False},
            {"stdin": "0 1 0 3 2 3", "expected_output": "4", "is_sample": False},
            {"stdin": "3 2 1", "expected_output": "1", "is_sample": False},
        ],
    },
    {
        "slug": "edit-distance",
        "title": "Edit Distance",
        "difficulty": "hard",
        "description": (
            "Read two lowercase words on separate lines. Print the minimum "
            "number of single-character insertions, deletions, or "
            "substitutions needed to turn the first word into the second "
            "(Levenshtein distance).\n\nExample\nInput:\nhorse\nros\nOutput: 3"
        ),
        "starter_code": "a = input()\nb = input()\n# your code here\n",
        "reference_solution": (
            "a = input()\n"
            "b = input()\n"
            "n, m = len(a), len(b)\n"
            "dp = [[0] * (m + 1) for _ in range(n + 1)]\n"
            "for i in range(n + 1):\n"
            "    dp[i][0] = i\n"
            "for j in range(m + 1):\n"
            "    dp[0][j] = j\n"
            "for i in range(1, n + 1):\n"
            "    for j in range(1, m + 1):\n"
            "        if a[i - 1] == b[j - 1]:\n"
            "            dp[i][j] = dp[i - 1][j - 1]\n"
            "        else:\n"
            "            dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])\n"
            "print(dp[n][m])\n"
        ),
        "test_cases": [
            {"stdin": "horse\nros", "expected_output": "3", "is_sample": True},
            {
                "stdin": "intention\nexecution",
                "expected_output": "5",
                "is_sample": False,
            },
            {"stdin": "abc\nabc", "expected_output": "0", "is_sample": False},
            {"stdin": "kitten\nsitting", "expected_output": "3", "is_sample": False},
            {"stdin": "a\nb", "expected_output": "1", "is_sample": False},
        ],
    },
    {
        "slug": "kth-largest-element",
        "title": "Kth Largest Element",
        "difficulty": "hard",
        "description": (
            "Read an integer `k`, then a line of space-separated integers. "
            "Print the k-th largest element (k=1 means the largest).\n\n"
            "Example\nInput:\n2\n3 2 1 5 6 4\nOutput: 5"
        ),
        "starter_code": "k = int(input())\nnums = list(map(int, input().split()))\n# your code here\n",
        "reference_solution": (
            "k = int(input())\n"
            "nums = list(map(int, input().split()))\n"
            "print(sorted(nums, reverse=True)[k - 1])\n"
        ),
        "test_cases": [
            {"stdin": "2\n3 2 1 5 6 4", "expected_output": "5", "is_sample": True},
            {"stdin": "1\n1 2 3", "expected_output": "3", "is_sample": False},
            {
                "stdin": "4\n3 2 3 1 2 4 5 5 6",
                "expected_output": "4",
                "is_sample": False,
            },
            {"stdin": "3\n1 2 3 4 5", "expected_output": "3", "is_sample": False},
            {"stdin": "1\n5 5 5", "expected_output": "5", "is_sample": False},
        ],
    },
    {
        "slug": "course-schedule-cycle",
        "title": "Course Schedule — Cycle Detection",
        "difficulty": "hard",
        "description": (
            "Read integers `n` (number of courses, labeled 0..n-1) and `m` "
            "(number of prerequisite pairs), then `m` lines each with two "
            "integers `a b` meaning course `a` requires course `b` first. "
            "Print `yes` if all courses can be finished (no cyclic "
            "dependency), otherwise print `no`.\n\n"
            "Example\nInput:\n2 1\n1 0\nOutput: yes"
        ),
        "starter_code": (
            "n, m = map(int, input().split())\n"
            "edges = [tuple(map(int, input().split())) for _ in range(m)]\n"
            "# your code here\n"
        ),
        "reference_solution": (
            "n, m = map(int, input().split())\n"
            "edges = [tuple(map(int, input().split())) for _ in range(m)]\n"
            "graph = [[] for _ in range(n)]\n"
            "for a, b in edges:\n"
            "    graph[a].append(b)\n"
            "state = [0] * n  # 0=unvisited, 1=visiting, 2=done\n"
            "ok = True\n"
            "def dfs(u):\n"
            "    global ok\n"
            "    state[u] = 1\n"
            "    for v in graph[u]:\n"
            "        if state[v] == 1:\n"
            "            ok = False\n"
            "            return\n"
            "        if state[v] == 0:\n"
            "            dfs(v)\n"
            "    state[u] = 2\n"
            "for u in range(n):\n"
            "    if state[u] == 0:\n"
            "        dfs(u)\n"
            "print('yes' if ok else 'no')\n"
        ),
        "test_cases": [
            {"stdin": "2 1\n1 0", "expected_output": "yes", "is_sample": True},
            {"stdin": "2 2\n1 0\n0 1", "expected_output": "no", "is_sample": False},
            {"stdin": "3 0", "expected_output": "yes", "is_sample": False},
            {
                "stdin": "3 3\n0 1\n1 2\n2 0",
                "expected_output": "no",
                "is_sample": False,
            },
            {"stdin": "4 2\n1 0\n3 2", "expected_output": "yes", "is_sample": False},
        ],
    },
]


def run():
    db = SessionLocal()
    created, skipped = 0, 0
    try:
        for p in PROBLEMS:
            if db.query(Problem).filter(Problem.slug == p["slug"]).first():
                skipped += 1
                continue
            problem = Problem(
                slug=p["slug"],
                title=p["title"],
                description=p["description"],
                difficulty=p["difficulty"],
                language="python3",
                starter_code=p["starter_code"],
                reference_solution=p["reference_solution"],
                source="open-source-classic",
            )
            db.add(problem)
            db.flush()
            for tc in p["test_cases"]:
                db.add(
                    TestCase(
                        problem_id=problem.id,
                        stdin=tc["stdin"],
                        expected_output=tc["expected_output"],
                        is_sample=tc["is_sample"],
                    )
                )
            created += 1
        db.commit()
        print(f"Seeded {created} problems ({skipped} already existed, skipped).")
    finally:
        db.close()


if __name__ == "__main__":
    run()
