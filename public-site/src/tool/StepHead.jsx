/* Heading block shared by every tool step: a numbered mono kicker and a big
   serif title on the left, the lead (and anything in `aside`) on the right. */

export default function StepHead({ id, num, kicker, title, children, aside = null }) {
  return (
    <header className="stephead">
      <div className="stephead__main">
        <p className="kicker kicker--rule">
          <span className="kicker__num">{num}</span> {kicker}
        </p>
        <h1 id={id} className="h1 stephead__title">{title}</h1>
      </div>
      <div className="stephead__aside">
        {children && <p className="lead">{children}</p>}
        {aside}
      </div>
    </header>
  )
}
