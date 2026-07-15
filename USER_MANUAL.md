# bisnes.ai User Manual

## 1. Introduction

bisnes.ai is an intelligent workspace assistant designed to help entrepreneurs, MSMEs, business owners, analysts, and professionals complete everyday knowledge work in one place. It combines conversational AI, document analysis, live web research, file generation, image understanding, image generation, Google Workspace automation, and long-term user memory.

The system is built around a simple chat interface, but its capabilities go beyond normal question answering. You can upload documents, ask the AI to analyse PDFs, generate reports, create downloadable files, send emails, create Google Docs or Sheets, schedule meetings, generate images, analyse screenshots, and continue work across previous conversations.

This manual explains every major feature from a user perspective and gives practical examples of how to use the system effectively.

## 2. Main Interface

When you open bisnes.ai, you will see the main chat workspace. The interface is organized into several areas:

- Sidebar: Access new chats, search history, Agent mode, conversation history, account settings, and connectors.
- Chat area: View your conversation, generated files, sources, images, and system responses.
- Composer: Type messages, upload files, select modes, choose skills, and send requests.
- Account menu: Manage language, connectors, profile settings, login methods, and account data.

The interface supports English, Chinese, and Bahasa Malaysia. You can switch language from the settings menu.

## 3. Account And Login

bisnes.ai can be used as a guest for a limited number of normal chat questions. To unlock file uploads, Agent mode, connectors, image uploads, and advanced workflows, you need to log in.

### 3.1 Email Registration

To create an account with email:

1. Click Sign up.
2. Enter your email address and password.
3. Check your email for a six-digit verification code.
4. Enter the OTP code on the verification page.
5. After verification, you will be signed in automatically.

The verification code is time-limited. If it expires, request a new one from the verification page.

### 3.2 Email Login

To sign in with an existing account:

1. Click Sign in.
2. Choose email login.
3. Enter your email and password.
4. Submit the form.

### 3.3 Google Login

You can also sign in with Google. Google sign-in is used for identity only. Google Workspace connectors, such as Gmail or Drive, require separate authorization from the Connectors panel.

### 3.4 Account Profile

From the account page, you can:

- View your profile name and email.
- Update your display name.
- Change your email address with OTP verification.
- Set or update a password.
- Link or unlink a Google account.
- Download your account data.
- Delete your account.

## 4. Language

bisnes.ai supports:

- English
- Chinese
- Bahasa Malaysia

The assistant tries to answer in the same language as your latest message. You can change the interface language from the settings menu.

## 5. Normal Chat

Normal chat is used for general questions, writing, explanation, brainstorming, translation, coding help, business advice, and everyday assistance.

Examples:

- "Explain cash flow in simple terms."
- "Write a professional email to a supplier."
- "Summarize the difference between sole proprietorship and Sdn Bhd."
- "Help me draft a marketing plan for a small cafe."

Normal chat can also use internal memory and knowledge base retrieval when relevant.

## 6. Web Search Mode

Web Search mode allows bisnes.ai to search live web sources before answering. It is useful for current or time-sensitive questions.

Use Web Search mode for:

- Latest news
- Current prices
- Regulations and policies
- Company or government updates
- Recent product information
- Location-sensitive recommendations
- Current schedules, rates, or announcements

Examples:

- "What are the latest MSME grants in Malaysia?"
- "Find the current SSM registration requirements."
- "Compare the latest business bank accounts for small businesses in Malaysia."

When web search is used, bisnes.ai shows source cards above the answer. These sources can be opened in a new tab.

### 6.1 Source-Based Answers

When sources are available, the assistant is expected to ground the answer in those sources. It should avoid inventing facts and should mention when something was not found in the retrieved material.

## 7. Think Mode

Think Mode is available only when the active model supports visible thinking tags. If the current model does not support this feature, the Think toggle is hidden.

When available, Think Mode can show a collapsible reasoning panel before the final answer. It is useful for complex analysis, planning, comparison, and multi-step questions.

Examples:

- "Compare these two business strategies and recommend one."
- "Analyse this financial decision step by step."
- "Help me plan a three-month launch roadmap."

## 8. Chat History

Logged-in users can save and reload conversations.

The sidebar history panel allows you to:

- View previous chats.
- Open an old conversation.
- Rename a chat.
- Delete a chat.
- Search past conversations.

Conversation history can include normal messages, uploaded attachments, generated PDFs, generated images, generated files, and source links.

## 9. Message Actions

Each message may include action buttons.

For user messages:

- Edit and resend the message.
- Copy message content.

For assistant messages:

- Copy the answer.
- Regenerate the answer.
- Resume a paused or interrupted response.
- Like or dislike the response.

Feedback helps track response quality.

## 10. Pausing And Resuming Generation

While the assistant is generating a response, the send button changes into a pause button. Click it to stop generation.

If a response is paused or interrupted, you can resume it from the message action buttons. The assistant will continue from the partial answer instead of starting over.

## 11. File Uploads

Logged-in users can upload files through the upload menu.

Supported upload types include:

- PDF
- CSV
- TSV
- Excel files
- JSON
- JSONL
- TXT
- Markdown
- Images

Uploaded files are shown as attachment cards above your message. PDFs can be clicked for preview.

## 12. Image Upload And Image Analysis

You can upload or paste images into the composer. If you upload an image without typing a question, bisnes.ai automatically uses a default image analysis prompt.

The image analysis feature can:

- Describe visible content.
- Extract text from screenshots or documents.
- Read labels, charts, invoices, receipts, menus, and forms.
- Compare multiple images.
- Identify important visual details.
- Mention uncertainty when an image is blurry, cropped, or unreadable.

Examples:

- "Extract the text from this receipt."
- "What does this chart show?"
- "Compare these two product mockups."
- "Analyse this screenshot and tell me what is wrong."

## 13. Image Generation

bisnes.ai can generate images locally. Depending on the configured image provider, it may use Stable Diffusion 3.5 Medium or a ComfyUI workflow.

You can request:

- Logos
- Posters
- Icons
- Banners
- Illustrations
- Social graphics
- Product visuals
- Website hero images

Examples:

- "Generate a modern logo for an MSME accounting app."
- "Create a clean poster for a small business workshop."
- "Design a blue and white technology icon."

Generated images appear as image cards with options to open or download the file.

## 14. Editing Generated Images

If a generated image already exists in the conversation, bisnes.ai can understand follow-up edit requests.

Examples:

- "Make the background white."
- "Change the company name to MSME Hub."
- "Use gold instead of blue."
- "Remove the text."
- "Make it more premium."

The system attempts to route these messages as edits to the previous generated image instead of treating them as normal chat.

## 15. Agent Mode

Agent Mode is the advanced workspace mode. It is designed for document workflows, file generation, Google Workspace actions, PDF analysis, structured data analysis, and multi-step tasks.

In Agent Mode:

- Web Search is disabled by default.
- The assistant focuses on uploaded files, generated outputs, and workspace actions.
- Google Workspace requests can be intercepted and executed directly.
- PDF and data workflows use specialized agents.
- Skill-based file generation becomes available.

Use Agent Mode when you want the system to do work, not just answer a question.

Examples:

- "Analyse this PDF and generate a report."
- "Create a PDF from the previous answer."
- "Generate a PPTX about this business plan."
- "Send the generated report to my email."
- "Upload this image to Google Drive."

## 16. PDF Agent

The PDF Agent is designed for deeper PDF analysis and report generation.

### 16.1 Uploading A Source PDF

When you upload a PDF in Agent Mode, the system extracts its text and tables. It then analyses the document and may ask whether you want to use a template.

The PDF Agent can detect document types such as:

- Financial reports
- Annual reports
- Business plans
- Academic documents
- Legal documents
- Medical documents
- General documents

### 16.2 Template-Based PDF Generation

If you have a report template, upload the source PDF first, then upload the template PDF when asked.

The system reads the template layout and table structure but avoids copying template placeholder content into the final report.

### 16.3 Direct PDF Generation

If you do not have a template, tell the assistant:

- "No template, generate directly."
- "Use a professional layout."
- "Proceed without a template."

The assistant will generate a structured report and automatically create a downloadable PDF.

### 16.4 Regenerating PDFs

You can ask the system to revise or regenerate a PDF.

Examples:

- "Regenerate the report with a more formal tone."
- "Update the PDF and add recommendations."
- "Create another version."

The new PDF appears as a download card.

## 17. Financial Data Agent

The Financial Data Agent handles structured business and financial files. It is separate from the PDF Agent and is used when you upload CSV, Excel, JSON, JSONL, TXT, or Markdown financial data in Agent Mode.

It can:

- Extract available business information.
- Build a structured financial table.
- Detect missing fields.
- Ask follow-up questions.
- Calculate available metrics.
- Provide preliminary financial summaries.
- Identify risks and gaps.
- Generate practical recommendations.
- Include chart blocks that can later be rendered into PDFs.

Useful fields include:

- Business name
- Period
- Revenue or sales
- Cost of goods sold
- Gross profit
- Operating expenses
- Net profit
- Cash balance
- Assets
- Liabilities
- Loans or debt
- Employees
- Products or services
- Market notes

Example:

"I uploaded my sales CSV. Analyse my business performance and tell me what information is missing."

## 18. Local File Generation Skills

In Agent Mode, the upload menu includes Skills. These allow bisnes.ai to create downloadable files directly.

Supported file types:

- DOCX
- PDF
- PPTX
- XLSX

### 18.1 DOCX Generation

Use DOCX when you need a Word-style document.

Examples:

- "Create a DOCX business proposal for a bakery."
- "Generate a Word document summarizing this conversation."

### 18.2 PDF Generation

Use PDF when you need a fixed-format report or document.

Examples:

- "Create a PDF report from the previous answer."
- "Generate a PDF checklist for company registration."

### 18.3 PPTX Generation

Use PPTX when you need a presentation deck.

Examples:

- "Create a PPTX pitch deck for my cafe."
- "Generate a presentation about MSME financing options."

### 18.4 XLSX Generation

Use XLSX when you need a spreadsheet.

Examples:

- "Create an XLSX budget tracker."
- "Generate an Excel table for monthly sales projections."

Generated files appear as file cards with open and download options.

## 19. Renaming Generated Files

After generating a DOCX, PDF, PPTX, or XLSX file, you can ask bisnes.ai to rename it.

Examples:

- "Rename the file to Business Plan 2026."
- "Save it as Cafe Monthly Budget."
- "Change the filename to Investor Pitch Deck."

The system creates a new stored copy with the requested name and updates the file card.

## 20. Recent Files

The upload menu includes Recent Files. This lets you reuse recently uploaded or generated files from your chat history.

In normal mode, recent image files are especially useful for image follow-up work. In Agent Mode, recent PDFs, generated files, and images can be reused for workspace tasks.

## 21. Google Workspace Connectors

Google Workspace connectors allow bisnes.ai to perform actions in your Google account.

Available connectors include:

- Google Drive
- Gmail
- Google Docs
- Google Sheets
- Google Slides
- Google Calendar
- Google Meet

Before using a connector, you must:

1. Log in.
2. Link your Google account from Account Settings if not already linked.
3. Open the Connectors panel.
4. Enable the specific connector you need.
5. Complete the Google authorization flow.

Each connector requests only the scopes needed for that service.

## 22. Google Drive

The Google Drive connector uploads generated or attached files to your Drive.

Examples:

- "Upload the generated PDF to Google Drive."
- "Save this image to my Google Drive."
- "Put the latest generated file into Drive."

If no file is available, the assistant will ask you to generate or upload one first.

## 23. Gmail

The Gmail connector helps compose and send emails.

### 23.1 Composing Emails

Examples:

- "Write and send an email to customer@example.com about the meeting tomorrow."
- "Send the generated PDF report to finance@example.com."
- "Email this image to my team."

### 23.2 Gmail Confirmation Flow

For safety, bisnes.ai does not immediately send the email. It first creates a preview card showing:

- Recipient
- Subject
- Body preview
- Attachment status

You can then choose:

- Confirm Send
- Cancel

If you confirm, the email is sent through Gmail. If you cancel, the pending draft is removed.

### 23.3 Attachments

Gmail can attach:

- The latest generated PDF
- The latest generated image
- The latest generated DOCX, PPTX, XLSX, or other generated file

If the email has an attachment and the body is empty, the system creates a short professional cover message.

## 24. Google Docs

The Google Docs connector creates native Google Docs.

Examples:

- "Create a Google Doc from the previous analysis."
- "Write a new Google Docs document about SME financing."
- "Save this report into Google Docs and name it MSME Funding Summary."

The system can convert Markdown-style headings, bullet lists, numbered lists, bold text, and tables into a structured Google Doc.

## 25. Google Sheets

The Google Sheets connector supports both creating and working with spreadsheets.

### 25.1 Create Sheets

Examples:

- "Create a Google Sheet for monthly sales and expenses."
- "Turn this table into a Google Sheet."
- "Create a spreadsheet from the previous analysis."

### 25.2 Search Sheets

Examples:

- "Find my Google Sheet named Budget 2026."
- "List recent Google Sheets about sales."

### 25.3 Read Sheets

Examples:

- "Read the Google Sheet called Monthly Sales."
- "Read Sheet1 range A1:D20 from this spreadsheet."

### 25.4 Append Rows

Examples:

- "Append these rows to my Sales Tracker sheet."
- "Add this new transaction to the spreadsheet."

### 25.5 Update Ranges

Examples:

- "Update A1:C5 in the Budget sheet with this data."
- "Replace the first table in my spreadsheet."

## 26. Google Slides

The Google Slides connector can create native Google Slides presentations or import generated PPTX files.

### 26.1 Create Native Google Slides

Examples:

- "Create Google Slides for a business plan presentation."
- "Generate a Google Slides deck about MSME grants."

The system generates slide titles, concise bullets, and a visual theme.

### 26.2 Import Generated PPTX

If you created a PPTX using the local Skills feature, you can import it into Google Slides.

Example:

"Import the latest generated PPTX into Google Slides."

## 27. Google Calendar

The Calendar connector creates events in your Google Calendar.

Examples:

- "Schedule a meeting tomorrow at 3 PM called Supplier Follow-Up."
- "Create a calendar event next Monday at 10 AM for Business Plan Review."

The system converts relative dates into calendar event times using your browser timezone when available.

## 28. Google Meet

The Google Meet connector creates a Calendar event with a Google Meet conference link.

Examples:

- "Set up a Google Meet tomorrow at 2 PM with ali@example.com."
- "Create a video meeting next Friday for the finance review."

The result includes the meeting title, time, Meet link, and Calendar link.

## 29. Account Data Export

From the account page, you can request a data export. bisnes.ai emails a text export containing account information and recent chat history to your registered email address.

## 30. Account Deletion

You can delete your account from the account page. This removes:

- Your user account.
- Your chat history.
- Related feedback records.
- Pending verification data.

This action is permanent.

## 31. Privacy And Data Handling

bisnes.ai stores user data to provide persistent features.

Stored data may include:

- Account profile information.
- Chat history.
- Uploaded files.
- Generated files.
- Generated PDFs.
- Generated images.
- Feedback.
- Google connector credentials.
- Long-term memory facts extracted from conversation.

Google Workspace actions require explicit connector authorization. Gmail sending requires user confirmation before the email is sent.

## 32. Best Practices

To get the best results:

- Be specific about the output you want.
- Mention the desired file type when asking for a file.
- Use Agent Mode for documents, PDFs, Google tools, and file generation.
- Use Web Search for current information.
- Upload source documents before asking for detailed analysis.
- If you want a specific filename, say it clearly.
- For Gmail, include recipient, subject, and purpose.
- For Calendar or Meet, include date, time, duration, and participants if needed.
- For image generation, describe style, color, subject, and format.

## 33. Example Workflows

### 33.1 Analyse A PDF And Generate A Report

1. Log in.
2. Turn on Agent Mode.
3. Upload a PDF.
4. Ask: "Analyse this document and generate a professional report."
5. If asked about templates, choose whether to upload one.
6. Download the generated PDF.

### 33.2 Create A Business Presentation

1. Turn on Agent Mode.
2. Open Skills.
3. Select PPTX.
4. Ask: "Create a pitch deck for a small cafe seeking financing."
5. Download the generated PPTX.
6. Optionally ask: "Import the latest generated PPTX into Google Slides."

### 33.3 Send A Generated Report By Email

1. Generate a PDF report.
2. Enable the Gmail connector.
3. Ask: "Send the generated PDF to finance@example.com with a professional message."
4. Review the email preview.
5. Click Confirm Send.

### 33.4 Analyse Financial Data

1. Turn on Agent Mode.
2. Upload a CSV or Excel file.
3. Ask: "Analyse my business performance and identify missing information."
4. Review the structured table, missing fields, and recommendations.
5. Ask for a PDF report if needed.

### 33.5 Generate And Edit An Image

1. Ask: "Generate a modern logo for an MSME finance app."
2. Review the generated image.
3. Ask: "Make the background white and use gold accents."
4. Download the updated image.

## 34. Troubleshooting

### 34.1 I Cannot Use Agent Mode

Agent Mode requires login. Sign in or create an account.

### 34.2 Upload Is Not Working

Check that the file type is supported and the file size is within the interface limit. Try uploading again.

### 34.3 Google Connector Says Permission Is Missing

Open the Connectors panel and re-enable the required connector. Google may require authorization again if the token expired or the required scope was not granted.

### 34.4 Gmail Did Not Send Immediately

This is expected. bisnes.ai creates a draft preview first. Click Confirm Send to send the email.

### 34.5 No Web Sources Are Shown

The system only searches the web when Web Search mode is enabled and the query appears to need live information. If no sources are found, the assistant may answer without live citations or tell you that no reliable sources were available.

### 34.6 PDF Generation Failed

Try again with a shorter or clearer request. If the uploaded PDF is very large or scanned, extraction may be incomplete. You can also ask the assistant to generate a simpler report.

### 34.7 Image Generation Failed

Local image generation depends on the configured image backend. If the local model or ComfyUI server is unavailable, image generation may fail. Try again later or contact the system administrator.

## 35. Glossary

- Agent Mode: Advanced mode for documents, files, and workspace actions.
- Connector: A Google Workspace integration such as Gmail, Drive, Docs, Sheets, Slides, Calendar, or Meet.
- GridFS: The storage layer used for uploaded and generated files.
- RAG: Retrieval-Augmented Generation, where relevant knowledge is retrieved before answering.
- Web Search Mode: Live search mode for current information.
- Skill: A local file-generation capability such as DOCX, PDF, PPTX, or XLSX.
- Template PDF: A PDF used as a layout reference for report generation.

## 36. Summary

bisnes.ai is designed to be a practical AI workspace. You can use it as a conversational assistant, a document analyst, a file generator, a web researcher, an image tool, and a Google Workspace operator. For simple questions, use normal chat. For live information, enable Web Search. For documents, files, images, and Google actions, use Agent Mode.

