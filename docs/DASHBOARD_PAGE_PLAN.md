# Dashboard page plan

## The House

Make `/dashboard` the signed-in home. A shared sky fills the viewport. Each destination has a clickable cottage floating on its own cloud. Selecting a house opens its door, fills the screen with a warm light, and takes the visitor to that page. Returning to the House lets them pick another destination.

Start with the existing [transparent cottage](../assets/cloud-house/cottage-cloud.png) and [timber door](../assets/cloud-house/timber-door.svg). The [single-house preview](../assets/cloud-house/preview.html) demonstrates the pieces and timing. Build one reusable house link and use the same illustration for the first layout pass. Review the village at actual screen sizes before drawing individual cottages or chibi Jophiel.

## Destinations

| House | Route | First integration |
| --- | --- | --- |
| Chat | `/chat` | Enter the existing conversation. Preserve the shared chat and microphone lifecycle when returning home. |
| System | `/system` | Show this house to the owner. Keep the existing owner-only route guard and System page design. |
| Integrations | `/integrations` | Open the existing connections page. |
| Sessions | `/sessions` (new) | Provide an entry to the existing VM session controls. Support the current access checks. The destination represents a session with any supported VM. |

Meal Scan has no house. Its removal from the application is a separate change.

## First screen

- Use one continuous sky behind every house. Position each transparent cottage, its cloud, label, and door together so the pieces float as one object.
- On wide screens, arrange the houses in a shallow arc or staggered rows. Make Chat easy to spot on arrival. On phones, reflow to two columns or one scrollable column when a label would be too small. Keep every available destination reachable.
- Put one clearly readable destination name on each house. Try rounded, hand-lettered text that follows a timber sign and has enough contrast against the wood. Review a full-size and phone-size sample before finalizing the lettering.
- Make the entire cottage and its label one native link with a real destination URL. Give it a visible keyboard focus treatment. Keep the door decorative inside the link so each destination has a single focus stop.

## Door transition

1. On an ordinary click, tap, or Enter, hold the selected house steady and open its door inward. Other houses stay in place.
2. Spread amber light from the chosen doorway to an ivory full-screen overlay. Navigate at peak brightness, then fade the overlay away on the destination. Keep the overlay in the signed-in layout so it survives the route change.
3. Aim for about one second overall. Honor reduced-motion preferences with stationary houses and immediate navigation. Preserve normal browser behavior for modified clicks, direct links, and Back. Rapid taps should navigate only once.

## Implementation order

1. Replace the dashboard-to-chat redirect with the House page. Keep the mounted ChatPage and its active-route handling in SignedInLayout. Use the dashboard as the default destination after sign-in while preserving intentional deep links.
2. Build the responsive sky and reusable house links. Check legibility, spacing, and the owner/non-owner layouts with the existing artwork.
3. Add the shared transition and a consistent Back to House link on destination pages. Retire cross-page tabs as their destinations become reachable from the House.
4. Wire the Sessions house to a protected route that hosts the existing session panel. Confirm it works there before removing its old entry from Chat. Detailed VM page redesign follows the dashboard pass.

## Ready for review when

- The authenticated dashboard opens directly and each visible house leads to its named destination. System remains protected on direct visits.
- The sky shows through around every cottage at desktop and phone sizes. Door and label stay aligned with their own house during motion and resizing.
- Mouse, touch, keyboard, and reduced-motion navigation all work. Returning home restores an immediately usable village with closed doors.
- A live chat or voice session survives a round trip through the dashboard. Test the sign-in default, direct destination URLs, browser Back, and repeated activation during a transition.
