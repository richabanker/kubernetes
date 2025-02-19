import google.generativeai as genai
import os
from github import Github
from google.cloud import storage
import re

total_comments_posted = 0

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
                filename = file.filename
                if (
                    filename.endswith("_test.go")
                    or filename.endswith("_test.py")
                    or "/test/" in filename
                    or ("_generated" in filename and (filename.endswith(".go") or filename.endswith(".py")))
                ):
                    continue  # Skip this file
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
    Point out potential improvements and suggest changes, based on the guidelines and the previous PR comments history.
    Only suggest a change if there is a clear improvement that can be made.
    If you see lines that have issues, mention the line number in the format 'line <number>: <comment>'.
    Do not post generic comments that don't suggest specific improvements.
    ```diff
    {diff}
    ```
    """
    response = model.generate_content(prompt)
    return response.text if response.text else None

def post_github_review_comments(repo_name, pr_number, diff_file, review_comment, github_token):
    """Posts review comments to a GitHub pull request, annotating specific lines."""
    global total_comments_posted
    g = Github(github_token)
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    if review_comment:
        commits = list(pr.get_commits())
        if not commits:
            print(f"WARNING: No commits found for PR {pr_number}. Posting general issue comment for {diff_file.filename}.")
            pr.create_issue_comment(f"Review for {diff_file.filename}:\n{review_comment}")
            return

        latest_commit = commits[-1]
        diff_lines = diff_file.patch.splitlines()

        lines_to_comment = []
        comments = []
        for line in review_comment.split('\n'):
            if "line" in line.lower() and ":" in line:
                try:
                    # Use a regular expression to find "line <number>:"
                    match = re.search(r"line\s+(\d+)\s*:", line.lower())
                    if match:
                        line_num = int(match.group(1))
                        # Find the index of the first colon after "line <number>:"
                        colon_index = line.lower().find(":", match.end())
                        if colon_index != -1:
                            comment = line[colon_index + 1:].strip()
                            comments.append(comment)
                            lines_to_comment.append(line_num)
                except (ValueError, IndexError):
                    continue

        if lines_to_comment:
            comment_count = 0
            for line_num, comment in zip(lines_to_comment, comments):
                if comment_count >= 10 or total_comments_posted >= 10:
                    break

                try:
                    corrected_line_num = None
                    right_side_line = 0
                    diff_line_index = 0
                    hunk_right_start = 0

                    for diff_line in diff_lines:
                        if diff_line.startswith("@@"):
                            hunk_info = diff_line.split("@@")[1].strip()
                            right_side_info = hunk_info.split(" ")[1].strip()
                            hunk_right_start = int(right_side_info.split("+")[1].split(",")[0])
                            right_side_line = hunk_right_start - 1 #subtract 1 because first line in hunk adds one.
                        elif diff_line.startswith("+"):
                            right_side_line += 1
                            if right_side_line == line_num:
                                corrected_line_num = right_side_line
                                break
                        elif diff_line.startswith("-"): #removed line
                            pass #removed lines should not affect right side line numbering.
                        elif not diff_line.startswith("-"): #context line or + line
                            if diff_line.startswith(" "):
                                right_side_line += 1

                        diff_line_index += 1

                    if corrected_line_num:
                        pr.create_review_comment(
                            body=comment,
                            commit=latest_commit,
                            path=diff_file.filename,
                            line=corrected_line_num,
                            side="RIGHT",
                        )
                        comment_count += 1
                        total_comments_posted += 1
                    else:
                        print(
                            f"WARNING: Could not find correct line number for comment on line {line_num} in {diff_file.filename}"
                        )

                except Exception as e:
                    print(f"ERROR: Failed to create review comment for line {line_num} in {diff_file.filename}: {e}")

            print(f"Review comments for {diff_file.filename} posted successfully.")
        else:
            print(f"Review for {diff_file.filename} skipped, no improvements suggested.")

    else:
        print(f"Gemini API returned no response for {diff_file.filename}.")

def main():
    """Main function to orchestrate the Gemini PR review with annotations."""
    global total_comments_posted
    total_comments_posted = 0
    api_key = os.environ.get('GEMINI_API_KEY')
    pr_number = int(os.environ.get('PR_NUMBER'))
    repo_name = os.environ.get('GITHUB_REPOSITORY')
    github_token = os.environ.get('GITHUB_TOKEN')

    # Use the GCS client library
    guidelines = download_and_combine_guidelines("hackathon-2025-sme-code-review-train", "guidelines/")
    if not guidelines:
        print("Warning: No guidelines loaded. Review will proceed without guidelines.")

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
