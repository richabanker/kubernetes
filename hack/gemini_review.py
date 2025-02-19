import google.generativeai as genai
import os
from github import Github
from google.cloud import storage
import re

# Set the maximum number of comments to post
MAX_COMMENTS = 20

total_comments_posted = 0  # Initialize the variable here

def get_pr_latest_commit_diff_files(repo_name, pr_number, github_token):
    """Retrieves diff information for each file in the latest commit of a PR, excluding test files and generated files."""
    g = Github(github_token)
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    try:
        commits = list(pr.get_commits())
        if commits:
            latest_commit = commits[-1]
            files = latest_commit.files
            diff_files = []
            for file in files:
                if not file.filename.endswith("_test.go") and not file.filename.endswith("_test.py") and not "/test/" in file.filename and "_generated" not in file.filename:
                    if file.patch:
                        diff_files.append(file)
            return diff_files
        else:
            return None
    except Exception as e:
        print(f"Error getting diff files from latest commit: {e}")
        return None

def download_and_combine_guidelines(bucket_name, prefix):
    """Downloads markdown files from GCS using the google-cloud-storage library."""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)  # Use prefix for efficiency
        
        guidelines_content = ""
        for blob in blobs:
            if blob.name.endswith(".md"):
                guidelines_content += blob.download_as_text() + "\n\n"
        return guidelines_content
        
    except Exception as e:
        print(f"Error downloading or combining guidelines: {e}")

def download_and_combine_pr_comments(bucket_name, prefix):
    """Downloads text files from GCS using the google-cloud-storage library."""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)  # Use prefix for efficiency
        pr_comments_content = ""
        # TODO: Skip for now, since it is too large
        # for blob in blobs:
        #     if blob.name.endswith(".txt"):
        #         pr_comments_content += blob.download_as_text() + "\n\n"
        return pr_comments_content
    except Exception as e:
        print(f"Error downloading or combining PR comments: {e}")
        return ""

def generate_gemini_review_with_annotations(diff_file, api_key, guidelines, pr_comments):
    """Generates a code review with annotations using Gemini."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')

    diff = diff_file.patch
    max_diff_length = 100000
    if len(diff) > max_diff_length:
        diff = diff[:max_diff_length] + "\n... (truncated due to length limit)..."

    prompt = f"""
    You are an expert Kubernetes API reviewer. Follow these guidelines:

    {guidelines}

    Review the following code diff from `{diff_file.filename}`. 

    Your task is to identify potential issues and suggest concrete improvements. 

    Prioritize comments that highlight potential bugs, suggest improvements.

    Avoid general comments that simply acknowledge correct code or good practices.

    Provide your review comments in the following format:

    ```
    line <line_number>: <comment>
    line <line_number>: <comment>
    ...and so on
    ```

    Ensure:
    * `spec` changes are valid.
    * `status` updates are accurate.
    * Object references are correct.
    * Validation is accurate.
    * Duration fields use `fooSeconds`.
    * Condition types are `PascalCase`.
    * API objects have required metadata.

    ```diff
    {diff}
    ```
    """
    response = model.generate_content(prompt)
    if response and response.text:
        return response.text
    else:
        print("=== Gemini Response (Empty) ===")
        return None

def post_github_review_comments(repo_name, pr_number, diff_file, review_comment, github_token):
    """Posts review comments to GitHub PR, annotating specific lines."""
    g = Github(github_token)
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    if review_comment:
        commits = list(pr.get_commits())
        if not commits:
            print(f"WARNING: No commits for PR {pr_number}. Posting general comment for {diff_file.filename}.")
            pr.create_issue_comment(f"Review for {diff_file.filename}:\n{review_comment}")
            return

        latest_commit = commits[-1]
        diff_lines = diff_file.patch.splitlines()

        # Use regex to find line numbers and comments
        line_comments = [(int(match.group(1)), match.group(2).strip())
                         for match in re.finditer(r"line (\d+): (.*)", review_comment)]

        for line_num, comment in line_comments:
            if total_comments_posted >= MAX_COMMENTS:
                print("Comment limit reached.")
                break
            try:
                corrected_line_num = None
                right_side_line = 0

                for diff_line in diff_lines:
                    if diff_line.startswith("@@"):
                        # Extract right-side line number from hunk info
                        right_side_line = int(diff_line.split("+").split(",")) - 1
                    elif diff_line.startswith("+"):
                        right_side_line += 1
                        if right_side_line == line_num:
                            corrected_line_num = right_side_line
                            break

                if corrected_line_num:
                    pr.create_review_comment(
                        body=comment,
                        commit=latest_commit,
                        path=diff_file.filename,
                        line=corrected_line_num,
                        side="RIGHT",
                    )
                    total_comments_posted += 1
                else:
                    print(f"WARNING: Could not find line {line_num} in {diff_file.filename}")

            except Exception as e:
                print(f"ERROR: Failed to create comment for line {line_num} in {diff_file.filename}: {e}")

        print(f"Review comments for {diff_file.filename} posted.")
    else:
        print(f"Gemini returned no response for {diff_file.filename}.")

def main():
    """Main function to orchestrate Gemini PR review."""
    api_key = os.environ.get('GEMINI_API_KEY')
    pr_number = int(os.environ.get('PR_NUMBER'))
    repo_name = os.environ.get('GITHUB_REPOSITORY')
    github_token = os.environ.get('GITHUB_TOKEN')

    guidelines = download_and_combine_guidelines("hackathon-2025-sme-code-review-train", "guidelines/")
    if not guidelines:
        print("Warning: No guidelines loaded.")

    diff_files = get_pr_latest_commit_diff_files(repo_name, pr_number, github_token)
    if diff_files is None:
        print("Failed to retrieve PR diff files. Exiting.")
        return

    pr_comments = download_and_combine_pr_comments("hackathon-2025-sme-code-review-train", "pr_comments/")

    for diff_file in diff_files:
        review_comment = generate_gemini_review_with_annotations(diff_file, api_key, guidelines, pr_comments)
        post_github_review_comments(repo_name, pr_number, diff_file, review_comment, github_token)

if __name__ == "__main__":
    main()
