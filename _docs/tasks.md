# Backlog

## 1. Project scaffolding and passing test

Goal: Set up the backend and frontend projects with a single passing test each.

Description: Initialize the FastAPI backend with pyproject.toml, app/main.py returning a health-check endpoint, database.py connecting to MongoDB via Motor + Beanie, and one pytest that hits the health endpoint. Initialize the React frontend with Vite, TypeScript, and Tailwind, and add one Vitest that renders the App component. Create docker-compose.yml with backend and frontend services (MongoDB is external). Add .env.example with MONGO_URL, OPENAI_BASE_URL, OPENAI_API_KEY, JWT_SECRET.

## 2. User registration and login

Goal: Users can register, log in, and receive JWT tokens.

Description: Create the User Beanie Document model. Build POST /api/auth/register, POST /api/auth/login (returns access + refresh tokens), POST /api/auth/refresh, and GET /api/auth/me. Add the `get_current_user` dependency that decodes the JWT and loads the user from MongoDB. Write tests for each endpoint including invalid credentials.

## 3. Project and membership CRUD

Goal: Users can create projects and invite team members.

Description: Create the Project Beanie Document with an embedded members array (each entry has user_id and role: facilitator or member). Build POST /api/projects, GET /api/projects (list user's projects), GET /api/projects/{id}, POST /api/projects/{id}/members, and DELETE /api/projects/{id}/members/{uid}. The project creator is automatically the facilitator. Only project members can access a project.

## 4. Feedback cycle management

Goal: A facilitator can create and close weekly feedback cycles within a project.

Description: Create the Cycle Beanie Document (status: collecting | retro | closed). Build POST /api/projects/{id}/cycles, GET /api/projects/{id}/cycles, GET /api/cycles/{id}, and PATCH /api/cycles/{id} to close a cycle. Only the facilitator can create or close cycles. A project can have at most one cycle in collecting or retro status at a time.

## 5. Submit and manage feedback cards

Goal: Team members can submit, edit, and delete Start/Stop/Continue feedback cards.

Description: Create the FeedbackCard Beanie Document. Build POST /api/cycles/{id}/feedback, GET /api/cycles/{id}/feedback, PATCH /api/feedback/{id}, and DELETE /api/feedback/{id}. During the collecting phase, GET returns only the current user's cards. Each card has a category (start/stop/continue) and an is_anonymous flag. When is_anonymous is true, author_id is stored as None so anonymity is enforced at the database level.

## 6. Start retrospective and reveal feedback

Goal: The facilitator can start a retrospective and reveal all feedback cards to the team.

Description: Create the Retrospective Beanie Document (phase: reveal | cluster | vote | discuss | done) with embedded arrays for clusters, votes, topics, decisions, and actions. Build POST /api/cycles/{id}/retro (creates retro, transitions cycle status to retro) and PATCH /api/retros/{id}/phase (advances phase linearly, no going back). When the retro starts, all cards become visible to all participants via GET /api/cycles/{id}/feedback.

## 7. Clustering feedback cards

Goal: Cards can be grouped into named clusters, manually or via AI suggestion.

Description: Build POST /api/retros/{id}/clusters (adds to embedded clusters array), PATCH /api/clusters/{id} (rename), DELETE /api/clusters/{id} (ungroups cards), and PATCH /api/feedback/{id}/cluster (move card into a cluster). Only available during the cluster phase. Any participant can move cards. Build POST /api/retros/{id}/clusters/suggest which calls the OpenAI-compatible proxy (gpt-5.4) to propose cluster groupings. The AI result is a suggestion the facilitator can accept or ignore.

## 8. Voting on clusters

Goal: Each team member can cast up to 3 stackable votes on clusters.

Description: Votes are embedded in the Retrospective document as an array of { user_id, cluster_id }. Build POST /api/retros/{id}/votes and GET /api/retros/{id}/votes/results. Max 3 votes per user validated in app logic, stackable on one cluster. Votes can only be submitted during the vote phase. Results are hidden until the facilitator advances to the discuss phase or all members have voted. Results return clusters ranked by total votes.

## 9. Discussion, decisions, and action items

Goal: The facilitator can run the discussion and record decisions and action items.

Description: Topics, decisions, and actions are embedded arrays in the Retrospective document. When voting closes, one topic is auto-created per cluster, ordered by votes. Build PATCH /api/topics/{id} (mark discussed/skipped/deferred, add notes), POST and PATCH for decisions (with is_confirmed flag), and POST and PATCH for action items (description, owner, optional due date, status open/done). Only the facilitator can manage topics and decisions. Action owners can update their own items.

## 10. Transcript paste and AI extraction

Goal: The facilitator can paste a meeting transcript and get AI-extracted decisions and actions.

Description: Build POST /api/retros/{id}/transcript accepting pasted transcript text. Store the transcript in the retro document. In a BackgroundTask, call the OpenAI-compatible proxy (gpt-5.4) to extract decisions, action items, owners, and due dates. Store results as draft suggestions in the retro's ai_suggestions field. Build GET /api/retros/{id}/suggestions and POST /api/retros/{id}/suggestions/confirm for the facilitator to review and approve. Audio/video upload deferred to a future task.

## 11. Retrospective summary

Goal: The facilitator can publish a retrospective summary visible to all project members.

Description: Build GET /api/retros/{id}/summary which assembles: top discussion topics (by votes), notes, confirmed decisions, confirmed action items, participation stats, and original feedback cards. Build POST /api/retros/{id}/summary/publish which advances the retro phase to done and marks the cycle as closed. After publishing, all project members can view the summary.

## 12. WebSocket real-time updates

Goal: Participants in a retrospective see live updates without refreshing.

Description: Build a WebSocket endpoint at ws://host/ws/retro/{retro_id}?token={jwt}. Authenticate via the JWT query param. Maintain a room (dict of connections) per retro. After each REST mutation during a retro (phase change, card move, cluster create/rename/delete, voting closed, topic status change), broadcast the event to all connected clients. The frontend receives these events and updates state without polling.

## 13. Frontend — auth pages and routing

Goal: Users can register, log in, and navigate the app with protected routes.

Description: Set up React Router with public routes (login, register) and protected routes (everything else). Build LoginPage and RegisterPage with forms that call the backend auth endpoints. Store the access token in memory and refresh token in an httpOnly cookie. Create an axios instance with an interceptor that attaches the token and handles 401 by attempting a refresh. Add a simple nav bar showing the logged-in user and a logout button.

## 14. Frontend — project page

Goal: Users can see their projects, create new ones, and manage members.

Description: Build ProjectPage showing the list of projects the user belongs to, with a "New Project" button. Each project card links to its detail view showing: current cycle status, submission progress, upcoming or active retro, list of past retros, and open action items. Add an "Invite Member" form (by email) and a member list with remove buttons for the facilitator.

## 15. Frontend — feedback form

Goal: Team members can submit Start/Stop/Continue cards during the collecting phase.

Description: Build FeedbackFormPage with three columns (Start, Stop, Continue). Each column has an input field and an "Add Card" button. Cards appear as editable items with a delete button and an "Anonymous" checkbox. The page calls the feedback API endpoints. Only the user's own cards are shown. The form is disabled if the cycle is not in collecting status.

## 16. Frontend — retro board (reveal, cluster, vote, discuss)

Goal: The retro board supports all four phases with real-time updates.

Description: Build RetroBoardPage that renders differently based on the retro phase. In reveal: show all cards grouped by category. In cluster: enable drag-and-drop (using dnd-kit) to move cards between clusters, with buttons to create/rename/delete clusters and a "Suggest Clusters" button for AI. In vote: show clusters with vote buttons, hide totals until closed. In discuss: show ranked topics, let facilitator mark each as discussed/skipped/deferred, and add notes, decisions, and action items. Connect to the WebSocket for live updates.

## 17. Frontend — transcript paste and summary

Goal: The facilitator can paste a transcript, review AI suggestions, and publish the summary.

Description: Build a transcript page with a textarea for pasting meeting transcript text. Once extraction completes, display suggested decisions and action items with confirm/edit/discard controls. Build SummaryPage showing the final retrospective summary with all confirmed data. Add a "Publish" button that closes the retro.

## 18. Docker Compose and environment configuration

Goal: The entire app runs locally with a single `docker compose up` command.

Description: Finalize docker-compose.yml with backend and frontend services (MongoDB is external). Create Dockerfiles for backend (Python + uvicorn) and frontend (Node build + nginx for prod, Vite dev server for dev). Set up environment variable handling via .env.example with placeholders for MONGO_URL, MONGO_DB_NAME, OPENAI_BASE_URL, OPENAI_API_KEY, JWT_SECRET. Ensure Beanie init runs on backend startup. Document the setup steps in a short README section.
