#include "selfdrive/ui/qt/widgets/driver_alert_dial.h"
#include <algorithm>

// Constructor: This initializes the widgets properties
DriverAlertDial::DriverAlertDial(QWidget *parent) : QWidget(parent),
    confidence(cereal::ModelDataV2::ConfidenceClass::GREEN),
    steering_torque(0.0),
    brake_pressure(0.0),
    acceleration(0.0) {

  setFixedSize(450, 450); //Set the widget size
}

// Updates the internal state of widget
void DriverAlertDial::updateState(cereal::ModelDataV2::ConfidenceClass conf,
                                  float steer_torque,
                                  float brake,
                                  float accel) {
  confidence = conf;
  steering_torque = steer_torque;
  brake_pressure = brake;
  acceleration = accel;
  update(); //this requests a repaint
}

// Helper function to draw a circle with a border
void DriverAlertDial::drawCircle(QPainter &painter,
                                  const QPointF &center,
                                  int radius,
                                  const QColor &fill_color,
                                  const QColor &border_color,
                                  int border_thickness,
                                  const QColor &shadow_color,
                                  int shadow_blur_radius,
                                  int shadow_x,
                                  int shadow_y) {
  // Draws shadow IF applicable
  if (shadow_blur_radius > 0 && shadow_color.alpha() > 0) {
    painter.setPen(Qt::NoPen);
    painter.setBrush(QBrush(shadow_color));
    painter.setRenderHint(QPainter::Antialiasing, true);

    int shadow_radius = radius + border_thickness / 2 + shadow_blur_radius;
    QRectF shadow_rect(center.x() - shadow_radius + shadow_x,
                       center.y() - shadow_radius + shadow_y,
                       shadow_radius * 2,
                       shadow_radius * 2);

    QPainterPath shadow_path;
    shadow_path.addEllipse(shadow_rect);
    painter.fillPath(shadow_path, shadow_color);
  }

  // Draws the circle with the border
  QPen pen(border_color, border_thickness);
  painter.setPen(pen);
  painter.setBrush(QBrush(fill_color));

  // Aligns the different circles drawn
  painter.drawEllipse(center.x() - radius, center.y() - radius, radius * 2, radius * 2);
}

// Returns the properties for the given confidence level
AlertProperties DriverAlertDial::getAlertProperties(cereal::ModelDataV2::ConfidenceClass conf) const {
  AlertProperties properties;
  switch (conf) {
    case cereal::ModelDataV2::ConfidenceClass::GREEN: // Low Alert
      properties.outerColor = QColor(21, 21, 21); // Outer fill color
      properties.middleColor = QColor(21, 21, 21); // Middle fill color
      properties.innerColor = QColor(16, 68, 79); // Inner fill color
      properties.borderColor = QColor(79, 16, 16); //Border color
      properties.shadowColor = QColor(0, 209, 255, 17); // Shadow color & Transparency
      properties.shadowBlurRadius = 25;
      properties.shadowX = 0;
      properties.shadowY = 0;
      properties.outerBorderThickness = 20;
      properties.middleBorderThickness = 10;
      properties.innerBorderThickness = 10;
      break;

    case cereal::ModelDataV2::ConfidenceClass::YELLOW: // Medium Alert
      properties.outerColor = QColor(21, 21, 21); // Outer fill color
      properties.middleColor = QColor(80, 77, 16); // Middle fill color
      properties.innerColor = QColor(21, 21, 21); // Inner fill color
      properties.borderColor = QColor(79, 16, 16); //Border color
      properties.shadowColor = QColor(255, 245, 0, 17); // Shadow color & Transparency
      properties.shadowBlurRadius = 25;
      properties.shadowX = 0;
      properties.shadowY = 0;
      properties.outerBorderThickness = 20;
      properties.middleBorderThickness = 10;
      properties.innerBorderThickness = 10;
      break;

    case cereal::ModelDataV2::ConfidenceClass::RED: // High Alert
      properties.outerColor = QColor(21, 21, 21); // Outer fill color
      properties.middleColor = QColor(13, 13, 13); // Middle fill color
      properties.innerColor = QColor(13, 13, 13); // Inner fill color
      properties.borderColor = QColor(255, 0, 0); //Border color
      properties.shadowColor = QColor(255, 0, 0, 17); // Shadow color & Transparency
      properties.shadowBlurRadius = 25;
      properties.shadowX = 0;
      properties.shadowY = 0;
      properties.outerBorderThickness = 20;
      properties.middleBorderThickness = 10;
      properties.innerBorderThickness = 10;
      break;

    default: // Disabled state
      properties.outerColor = QColor(108, 108, 108); // Outer fill color
      properties.middleColor = QColor(108, 108, 108); // Middle fill color
      properties.innerColor = QColor(108, 108, 108); // Inner fill color
      properties.borderColor = QColor(122, 97, 97); //Border color
      properties.shadowColor = QColor(0, 0, 0, 0); // Shadow color & Transparency
      properties.shadowBlurRadius = 0;
      properties.shadowX = 0;
      properties.shadowY = 0;
      properties.outerBorderThickness = 20;
      properties.middleBorderThickness = 10;
      properties.innerBorderThickness = 10;
      break;
  }
  return properties;

}

// Paints the widget
void DriverAlertDial::paintEvent(QPaintEvent *event) {
  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing);

  // Get properties based on confidence level
  AlertProperties properties = getAlertProperties(confidence);

  QPointF center(width() / 2, height() / 2);

  // Draws the outer
  drawCircle(painter,
            QPointF(width() / 2, height() / 2),
            175,
            properties.outerColor,
            properties.borderColor,
            properties.outerBorderThickness,
            properties.shadowColor,
            properties.shadowBlurRadius,
            properties.shadowX,
            properties.shadowY);

  // Draws the middle circle
  drawCircle(painter,
            QPointF(width() / 2, height() / 2),
            120,
            properties.middleColor,
            properties.borderColor,
            properties.middleBorderThickness,
            properties.shadowColor,
            properties.shadowBlurRadius / 4,
            properties.shadowX,
            properties.shadowY);

  // Draws the inner circle
  drawCircle(painter,
            QPointF(width() / 2, height() / 2),
            55,
            properties.innerColor,
            properties.borderColor,
            properties.innerBorderThickness,
            properties.shadowColor,
            properties.shadowBlurRadius / 4,
            properties.shadowX,
            properties.shadowY);

  // Draws the alert ball
  QColor alert_color = getAlertColor(confidence);
  painter.setBrush(alert_color);
  QPointF ball_pos = calculateAlertBallPosition();

  // This effects the alignment of alert ball
  painter.drawEllipse(ball_pos.x() - 20, ball_pos.y() - 20, 40, 40); // edit the ball size
}

// Determines what color alert ball based on confidence level
QColor DriverAlertDial::getAlertColor(cereal::ModelDataV2::ConfidenceClass conf) {
  switch (conf) {
    case cereal::ModelDataV2::ConfidenceClass::GREEN:
      return QColor(0, 255, 255); // Cyan
    case cereal::ModelDataV2::ConfidenceClass::YELLOW:
      return QColor(255, 255, 0); // Yellow
    case cereal::ModelDataV2::ConfidenceClass::RED:
      return QColor(255, 0, 0); // Red
    default:
      return QColor(0, 255, 255); // Default to Cyan
  }
}


// ---
// MODIFY - This should calculate the position of the alert ball
// ---

QPointF DriverAlertDial::calculateAlertBallPosition() const {
  float x = width() / 2;
  float y = height() / 2;

  // Define thresholds for different states
  float too_fast_threshold = 0.75;
  float too_slow_threshold = 0.25;
  float low_torque_threshold = -0.5;
  float high_torque_threshold = 0.5;

  // Scaling factor to control movement based on confidence
  float ball_movement_scale = 5; // Adjust as needed

  if (confidence != cereal::ModelDataV2::ConfidenceClass::GREEN) {
    if (acceleration > too_fast_threshold) {
      // Too fast, move the ball up
      y -= (acceleration - too_fast_threshold) * ball_movement_scale;
    } else if (acceleration < too_slow_threshold) {
      // Too slow, move the ball up
      y += (too_slow_threshold - acceleration) * ball_movement_scale;
    }

    if (steering_torque < low_torque_threshold) {
      // Not enough torque, turn left
      x -= (low_torque_threshold - steering_torque) * ball_movement_scale;
    } else if (steering_torque > high_torque_threshold) {
      // Not enough torque, turn right
      x += (steering_torque - high_torque_threshold) * ball_movement_scale;
    }
  }

  // Clamp the position within the widget bounds
  x = std::max(10.0f, std::min(x, width() - 10.0f));
  y = std::max(10.0f, std::min(y, height() - 10.0f));

  return QPointF(x, y);
}
