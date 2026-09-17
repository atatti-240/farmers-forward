import { TrowelDoodle, FlaskDoodle, ClipboardDoodle, SquiggleUnderline } from './doodles.jsx'

const STEPS = [
  {
    num: '1',
    title: 'You pull the samples',
    body:
      'Walk your field and scoop a little soil from each section. We help you ' +
      'lay out a simple grid, so the results speak for the whole field, not ' +
      'one lucky corner.',
    icon: TrowelDoodle,
  },
  {
    num: '2',
    title: 'We run the tests',
    body:
      'Every sample gets measured for pH, salinity, nitrogen, phosphorus and ' +
      'potassium. Then we score each section and work out what is actually ' +
      'holding it back.',
    icon: FlaskDoodle,
  },
  {
    num: '3',
    title: 'You get your plan',
    body:
      'A color-coded map of your field, the crops your soil can carry right ' +
      'now, and step-by-step fixes: what to add, how much, and when.',
    icon: ClipboardDoodle,
  },
]

export default function HowItWorks() {
  return (
    <section id="how-it-works" className="ff-section ff-how">
      <div className="ff-container">
        <div className="ff-how__head">
          <span className="ff-eyebrow">how it works</span>
          <h2 className="ff-how__title">Three steps, no paperwork.</h2>
          <SquiggleUnderline className="ff-how__squiggle" />
          <p className="ff-how__subtitle">
            From a bucket of dirt to a plan for the season. Here&rsquo;s
            the whole thing.
          </p>
        </div>

        <ol className="ff-how__grid">
          {STEPS.map(({ num, title, body, icon: Icon }) => (
            <li key={num} className="ff-notecard ff-step-card">
              <div className="ff-step-card__top">
                <span className="ff-step-card__num">{num}</span>
                <Icon className="ff-step-card__icon" size={42} />
              </div>
              <h3 className="ff-notecard__title ff-step-card__title">{title}</h3>
              <p className="ff-step-card__body">{body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}
