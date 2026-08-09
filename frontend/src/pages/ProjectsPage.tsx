/**
 * The first protected page, and a placeholder until #14 fills it in.
 *
 * It exists now so `/projects` is a real guarded route rather than a promise:
 * everything #13's guards, layout and logout are tested against needs somewhere
 * authenticated to land.
 */
export function ProjectsPage() {
  return (
    <section>
      <h1 className="text-2xl font-bold text-gray-900">Projects</h1>
      <p>Your projects will appear here.</p>
    </section>
  );
}
