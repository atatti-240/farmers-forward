import { SunDoodle, ArrowDoodle } from './doodles.jsx'

export default function Hero() {
  return (
    <header className="ff-hero">
      <div className="ff-container ff-hero__inner">
        <p className="ff-hero__hello">
          A small soil-testing project from Fort Bend County, Texas
        </p>

        <div className="ff-hero__title-row">
          <h1 className="ff-hero__title">
            Get to know <span className="ff-mark">the ground</span> you farm.
          </h1>
          <SunDoodle className="ff-hero__sun" size={72} />
        </div>

        <p className="ff-hero__tagline">
          We&rsquo;re Farmers Forward, and we test soil for small farms along the
          Brazos River for free. You pull a few scoops from your field, we
          measure what&rsquo;s in them, and you get an honest report on what your
          soil needs. That&rsquo;s the whole deal.
        </p>

        <div className="ff-hero__actions">
          <div className="ff-hero__cta-wrap">
            <a href="#contact" className="ff-btn ff-btn--sticker ff-hero__cta">
              Request a free soil test
            </a>
            <span className="ff-hero__note" aria-hidden="true">
              <ArrowDoodle className="ff-hero__note-arrow" size={40} />
              free means free. No invoice, ever
            </span>
          </div>
          <a href="#how-it-works" className="ff-btn ff-btn--quiet ff-hero__cta-ghost">
            See how it works
          </a>
        </div>

        <p className="ff-hero__facts">
          Every sample gets five measurements (pH, salinity, nitrogen,
          phosphorus and potassium), read against what your crop actually
          needs.
        </p>
      </div>
    </header>
  )
}
