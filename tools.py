from selenium.webdriver.common.by import By

def get_accessible_name(driver, element) -> str:
    aria_labelledby = element.get_attribute("aria-labelledby")
    if aria_labelledby:
        try:
            labels = [driver.find_element(By.ID, id_part).text.strip() for id_part in aria_labelledby.split()]
            if labels:
                return " ".join(labels)
        except:
            pass

    aria_label = element.get_attribute("aria-label")
    if aria_label:
        return aria_label.strip()

    element_id = element.get_attribute("id")
    if element_id:
        try:
            label = driver.find_element(By.CSS_SELECTOR, f"label[for='{element_id}']")
            if label:
                return label.text.strip()
        except:
            pass

    try:
        wrapping_label = element.find_element(By.XPATH, "ancestor::label")
        if wrapping_label:
            return wrapping_label.text.strip()
    except:
        pass

    try:
        parent = element.find_element(By.XPATH, "./parent::*")
        labels = parent.find_elements(By.TAG_NAME, "label")
        if labels:
            return " ".join([label.text.strip() for label in labels if label.text.strip()])
    except:
        pass

    placeholder = element.get_attribute("placeholder")
    if placeholder:
        return placeholder.strip()

    title = element.get_attribute("title")
    if title:
        return title.strip()

    return element.accessible_name