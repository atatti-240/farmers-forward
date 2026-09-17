import { SproutDoodle, FlaskDoodle, ClipboardDoodle, SquiggleUnderline } from './doodles.jsx'

const CARDS = [
  {
    icon: SproutDoodle,
    title: 'Free, start to finish',
    body:
      'Sampling help, the lab work, the report: every step costs you ' +
      'nothing. That is the whole point of the project.',
  },
  {
    icon: FlaskDoodle,
    title: 'Real measurements',
    body:
      'pH, salinity, nitrogen, phosphorus, potassium: measured from your ' +
      'actual soil, not guessed from a county average.',
  },
  {
    icon: ClipboardDoodle,
    title: 'Advice in plain English',
    body:
      'A plan you can act on this season: what to add, how much, and where, ' +
      'written for farmers, not for agronomy journals.',
  },
]

export default function Mission() {
  return (
    <section id="mission" className="ff-section ff-mission">
      <div className="ff-container ff-mission__grid">
        <div className="ff-mission__text">
          <span className="ff-eyebrow">why we do this</span>
          <h2 className="ff-mission__title">
            A proper soil test shouldn&rsquo;t cost more than the seed going into
            the ground.
          </h2>
          <SquiggleUnderline className="ff-mission__squiggle" />
          <p className="ff-mission__body">
            Farmers Forward started with a simple frustration: the small farms
            working some of the richest dirt in Texas, the{' '}
            <strong>Brazos River</strong> bottomland of{' '}
            <strong>Fort Bend County</strong>, often skip soil testing
            because lab fees and consultants add up fast. So the guesswork
            continues, and the fertilizer bill grows either way.
          </p>
          <p className="ff-mission__body">
            We do the testing and the explaining ourselves, and we hand you the
            results in plain English. No subscription, no sales call afterward,
            just your field&rsquo;s numbers and what to do about them.
          </p>
        </div>

        <ul className="ff-mission__cards">
          {CARDS.map(({ icon: Icon, title, body }) => (
            <li key={title} className="ff-notecard ff-mission__card">
              <Icon className="ff-mission__card-icon" size={40} />
              <div>
                <h3 className="ff-notecard__title">{title}</h3>
                <p>{body}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
