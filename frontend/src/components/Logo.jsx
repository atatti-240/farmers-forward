import logoImg from "../assets/logo.png";
import { useRef } from "react";

const Logo = ({ className, onClick, onSecretUnlock }) => {
  const clickCount = useRef(0);
  const clickTimer = useRef(null);

  const handleClick = () => {
    clickCount.current++;
    console.log('logo clicked', clickCount.current, 'times');
    if (clickTimer.current) clearTimeout(clickTimer.current);
    clickTimer.current = setTimeout(() => { clickCount.current = 0; }, 500);
    if (clickCount.current >= 3) {
      clickCount.current = 0;
      console.log('triple click! firing onSecretUnlock:', onSecretUnlock);
      if (onSecretUnlock) onSecretUnlock();
    }
    if (onClick) onClick();
  };

  return (
    <img
      src={logoImg}
      alt="Farmers Forward Logo"
      className={className}
      onClick={handleClick}
      style={{ height: "60px", width: "auto" }}
    />
  );
};

export default Logo;
