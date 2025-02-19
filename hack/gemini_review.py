import google.generativeai as genai
import os
from github import Github
from google.cloud import storage
import re

def get_pr_latest_commit_diff_files(repo_name, pr_number, github_token):
    """Retrieves diff information for each file in the latest commit of a PR, excluding test files."""
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
                if not file.filename.endswith("_test.go") and not file.filename.endswith("_test.py") and not "/test/" in file.filename and not file.filename.endswith("_generated.go") and not file.filename.endswith("_generated.deepcopy.go"):
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
        return ""

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
    """Generates a code review with annotations, incorporating guidelines."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')

    diff = diff_file.patch
    max_diff_length = 100000  # Adjust based on token count
    if len(diff) > max_diff_length:
        diff = diff[:max_diff_length]
        diff += "\n... (truncated due to length limit) ..."

    prompt = f"""
    The following are the API review guidelines:

    {guidelines}

    The following are the previous PR comments history:

    {pr_comments}

    Review the following code diff from file `{diff_file.filename}` and provide feedback.
    Point out potential issues, based on the guidelines and the previous PR comments history.
    If you see lines that have issues, mention the line number in the format 'line <number>: <comment>'.
    ```diff
    {diff}
    ```
    """
    # print("total_tokens: ", model.count_tokens(prompt))
    response = model.generate_content(prompt)
    return response.text if response.text else None

def post_github_review_comments(repo_name, pr_number, diff_file, review_comment, github_token):
    """Posts review comments to a GitHub pull request, annotating specific lines.
    Limits the number of comments per file to 10.
    """
    g = Github(github_token)
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    max_comments_per_file = 10  # Set the limit here

    if review_comment:
        commits = list(pr.get_commits())
        if not commits:
            pr.create_issue_comment(f"Review for {diff_file.filename}:\n{review_comment}")
            return

        latest_commit = commits[-1]
        line_comments = []
        for line in review_comment.split('\n'):
            match = re.search(r"line\s*(\d+)\s*:?\s*(.*)", line, re.IGNORECASE)  
            if match:
                line_num = int(match.group(1))
                comment = match.group(2).strip()
                line_comments.append((line_num, comment))

        if line_comments:
            diff_lines = diff_file.patch.splitlines()
            diff_line_numbers =
            original_file_line_num = 0  # Initialize here

            for line in diff_lines:
                if line.startswith("@@"):
                    # Extract the line number from the '@@' line
                    match = re.search(r"@@ -+,+ \+(+),+ @@", line)
                    if match:
                        original_file_line_num = int(match.group(1)) 
                elif line.startswith("+"):
                    # Only increment for additions
                    original_file_line_num += 1
                    diff_line_numbers.append(original_file_line_num)
                elif line.startswith(" "):
                    original_file_line_num += 1

            comment_count = 0
            for original_line_num, comment in line_comments:
                if comment_count >= max_comments_per_file:
                    print(f"Reached the maximum number of comments ({max_comments_per_file}) for {diff_file.filename}.")
                    break 

                try:
                    diff_index = diff_line_numbers.index(original_line_num) + 1 
                    pr.create_review_comment(body=comment, commit=latest_commit, path=diff_file.filename, line=diff_index, side="RIGHT")
                    comment_count += 1
                except ValueError:
                    print(f"WARNING: Line {original_line_num} not in diff for {diff_file.filename}. General comment.")
                    pr.create_issue_comment(f"Comment for {diff_file.filename} (line {original_line_num}):\n{comment}")
                except Exception as e:
                    print(f"ERROR: Failed comment for line {original_line_num} in {diff_file.filename}: {e}")

            print(f"Review comments for {diff_file.filename} posted.")
        else:
            pr.create_issue_comment(f"Review for {diff_file.filename}:\n{review_comment}")
            print(f"Review for {diff_file.filename} posted as general comment.")
    else:
        print(f"Gemini API returned no response for {diff_file.filename}.")

def main():
    """Main function to orchestrate the Gemini PR review with annotations."""
    api_key = os.environ.get('GEMINI_API_KEY')
    pr_number = int(os.environ.get('PR_NUMBER'))
    repo_name = os.environ.get('GITHUB_REPOSITORY')
    github_token = os.environ.get('GITHUB_TOKEN')

    # Use the GCS client library
    guidelines = download_and_combine_guidelines("hackathon-2025-sme-code-review-train", "guidelines/")
    if not guidelines:
        print("Warning: No guidelines loaded.  Review will proceed without guidelines.")

    diff_files = get_pr_latest_commit_diff_files(repo_name, pr_number, github_token)

    if diff_files is None:
        print("Failed to retrieve PR diff files from latest commit. Exiting.")
        return

    pr_comments = download_and_combine_pr_comments("hackathon-2025-sme-code-review-train", "pr_comments/")
    if not pr_comments:
        print("Warning: No PR comments loaded. Review will proceed without PR comments history.")

    for diff_file in diff_files:
        review_comment = generate_gemini_review_with_annotations(diff_file, api_key, guidelines, pr_comments)
        post_github_review_comments(repo_name, pr_number, diff_file, review_comment, github_token)

if __name__ == "__main__":
    main()
