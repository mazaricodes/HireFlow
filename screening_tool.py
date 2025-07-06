import os
import requests
import pdfplumber
import csv
from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename
import json
from dotenv import load_dotenv # Import load_dotenv

# Load environment variables from .env file at the start
load_dotenv()

app = Flask(__name__)

# It's better to get the API key from environment variables for security.
# Ensure you set the GEMINI_API_KEY environment variable before running the app.
# For local testing, you can replace "YOUR_GEMINI_API_KEY" with your actual key,
# but never commit your actual key to version control.
# After adding load_dotenv(), os.getenv("GEMINI_API_KEY") will pick it up from .env
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Global list to store results for CSV download
results = []

def chat_gpt(conversation):
    """
    Sends a conversation to the Gemini API and returns the generated text.

    Args:
        conversation (list): A list of message dictionaries, where each dictionary
                             has a 'role' (e.g., 'user', 'model') and 'content' (text).

    Returns:
        str: The generated text from the Gemini model, or an error message if the API call fails.
    """
    # Prepare the payload for the Gemini API
    # The 'contents' field expects a list of dictionaries, each with a 'parts' key
    # containing a list of part dictionaries (e.g., {'text': '...'}).
    api_contents = []
    for msg in conversation:
        # Gemini API expects 'role' to be 'user' or 'model'
        # and content to be in 'parts' list with 'text' key.
        api_contents.append({
            "role": msg.get("role", "user"), # Default to 'user' if role is missing
            "parts": [{"text": msg.get("content", "")}]
        })

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {
        "Content-Type": "application/json",
        # Correctly reference the GEMINI_API_KEY variable
        "X-goog-api-key": GEMINI_API_KEY
    }
    data = {
        "contents": api_contents
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()  # Raise an HTTPError for bad responses (4xx or 5xx)
        response_json = response.json()

        # Extract generated content from the Gemini API response
        # The structure is typically response_json['candidates'][0]['content']['parts'][0]['text']
        generated_text = ""
        if "candidates" in response_json and len(response_json["candidates"]) > 0:
            candidate = response_json["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"] and len(candidate["content"]["parts"]) > 0:
                # Assuming the first part contains the text response
                generated_text = candidate["content"]["parts"][0].get("text", "")
            else:
                generated_text = "Error: Unexpected content structure in Gemini API response."
        else:
            generated_text = "Error: No candidates found in Gemini API response."

        return generated_text

    except requests.exceptions.RequestException as e:
        print(f"Error calling Gemini API: {e}")
        return f"Error: Could not connect to Gemini API. {e}"
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from Gemini API response: {e}")
        return f"Error: Invalid JSON response from Gemini API. {e}"
    except Exception as e:
        print(f"An unexpected error occurred in chat_gpt: {e}")
        return f"Error: An unexpected error occurred. {e}"

def pdf_to_text(file_stream):
    """
    Extracts text from a PDF file stream.

    Args:
        file_stream: A file-like object (e.g., from request.files).

    Returns:
        str: The extracted text from the PDF.
    """
    text = ''
    # pdfplumber can open file-like objects directly
    with pdfplumber.open(file_stream) as pdf:
        for page in pdf.pages:
            extracted_page_text = page.extract_text()
            if extracted_page_text:
                text += extracted_page_text + "\n" # Add newline for readability between pages
    return text

def update_csv(results_data):
    """
    Writes the processed resume results to a CSV file.

    Args:
        results_data (list): A list of lists, where each inner list represents
                             a row for the CSV (Resume Name, Comments, Suitability).
    """
    # Using 'w' mode will overwrite the file each time.
    # If you want to append, use 'a' mode, but typically for a fresh report, 'w' is fine.
    with open('results.csv', 'w', newline='', encoding='utf-8') as csvfile:
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(["Resume Name", "Comments", "Suitability"])
        csv_writer.writerows(results_data)

@app.route('/upload', methods=['GET', 'POST'])
def upload_resume():
    """
    Handles resume uploads, processes them with Gemini, and returns results.
    For GET requests, it renders the upload form.
    """
    global results # Declare global to modify the `results` list
    if request.method == 'POST':
        resume_files = request.files.getlist('file[]')
        job_description = request.form.get('job_description')
        mandatory_keywords = request.form.get('mandatory_keywords')

        if not resume_files or not job_description or not mandatory_keywords:
            return jsonify({"error": "Please provide resume files, a job description, and mandatory keywords."}), 400

        results = [] # Clear previous results for a new upload session
        for resume_file in resume_files:
            # Read the PDF content directly from the file stream
            resume_text = pdf_to_text(resume_file)

            # Construct the conversation for Gemini
            conversation = [
                {"role": "user", "content": "You are a helpful assistant specialized in recruitment and talent management."},
                {"role": "user", "content": f"Mandatory keywords: {mandatory_keywords}"},
                {"role": "user", "content": f"Analyze the following resume against the job description and mandatory keywords. Provide very short comments on how well the resume matches the requirements, specifically mentioning if the mandatory keywords are present. At the end of your response, state clearly if the candidate is 'Suitable', 'Not Suitable', or 'Maybe Suitable'.\n\nJob Description: {job_description}\n\nResume: {resume_text}"}
            ]

            response_text = chat_gpt(conversation)

            # Replace newline characters with spaces for better CSV readability
            response_text = response_text.replace('\n', ' ').replace('\r', '')

            # Determine the suitability category
            suitability = "Unknown" # Default in case keywords are not found
            response_lower = response_text.lower()

            if "not suitable" in response_lower:
                suitability = "Not Suitable"
            elif "maybe suitable" in response_lower:
                suitability = "Maybe Suitable"
            elif "suitable" in response_lower: # Check for "suitable" last as it's a broader term
                suitability = "Suitable"

            results.append([resume_file.filename, response_text, suitability])

        return jsonify({"results": results})
    else:  # Handling the GET request to display the form
        return render_template('upload.html')

@app.route('/download_csv', methods=['GET'])
def download_csv():
    """
    Triggers the download of the generated CSV file.
    """
    global results
    if not results:
        return "No results to download. Please upload resumes first.", 404

    update_csv(results) # Ensure the CSV is up-to-date with current results
    return send_file(
        'results.csv',
        mimetype='text/csv',
        as_attachment=True,
        download_name='resume_screening_results.csv'
    )

@app.route('/')
def index():
    """
    Renders the main page of the application.
    """
    return render_template('upload.html')

if __name__ == '__main__':
    # When running locally, Flask defaults to 127.0.0.1:5000
    # For deployment, you'd use a production-ready WSGI server like Gunicorn.
    app.run(debug=True) # Set debug=True for development to see errors
