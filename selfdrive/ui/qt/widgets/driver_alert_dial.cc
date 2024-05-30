#include "selfdrive/ui/qt/widgets/driver_alert_dial.h"
#include <algorithm>

// Constructor: This initializes the widgets properties
DriverAlertDial::DriverAlertDial(QWidget *parent) : QWidget(parent), confidence(cereal::ModelDataV2::ConfidenceClass::GREEN), steering_torque(0.0), brake_pressure(0.0), acceleration(0.0) {
  //Set the widget size
  setFixedSize(360, 360);
}

// Updates the internal state of widget
void DriverAlertDial::updateState(cereal::ModelDataV2::ConfidenceClass conf, float steer_torque, float brake, float accel) {
  confidence = conf;
  steering_torque = steer_torque;
  brake_pressure = brake;
  acceleration = accel;
  update(); //this requests a repaint
}

// Helper function to draw a circle with a border
void DriverAlertDial::drawCircle(QPainter &painter, const QPointF &center, int radius, const QColor &fill_color, const QColor &border_color, int border_thickness) {
  painter.setBrush(QBrush(fill_color));
  if (border_thickness > 0) {
    painter.setPen(QPen(border_color, border_thickness));
  } else {
    painter.setPen(Qt::NoPen);
  }
  painter.drawEllipse(center, radius, radius);
}

// Paints the widget
void DriverAlertDial::paintEvent(QPaintEvent *event) {
  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing);


  // Define colors based on alert level
  QColor outer_color, middle_color, inner_color;
  QColor border_color = QColor(50, 50, 50); // Dark gray border color
  int border_thickness = 10;

  // Determine the colors based on confidence level
  switch (confidence) {
    case cereal::ModelDataV2::ConfidenceClass::GREEN: // Low Alert
      outer_color = QColor(0, 255, 255); // Cyan
      middle_color = QColor(0, 128, 128); // Dark Cyan
      inner_color = QColor(0, 64, 64); //Even Darker Cyan
      break;
    case cereal::ModelDataV2::ConfidenceClass::YELLOW: // Medium Alert
      outer_color = QColor(255, 255, 0); // Yellow
      middle_color = QColor(128, 128, 0); // Dark Yellow
      inner_color = QColor(64, 64, 0); //Even Darker Yellow
      break;
    case cereal::ModelDataV2::ConfidenceClass::RED: // High Alert
      outer_color = QColor(255, 0, 0); // Red
      middle_color = QColor(128, 0, 0); // Dark Red
      inner_color = QColor(64, 64, 0); //Even Darker Red
      break;
    default:
      outer_color = QColor(0, 255, 255); // Cyan
      middle_color = QColor(0, 128, 128); // Dark Cyan
      inner_color = QColor(0, 64, 64); //Even Darker Cyan
      break;
  }

  // Draw the outer, middle, and inner circles
  drawCircle(painter, QPointF(width() / 2, height() / 2), 175, outer_color, border_color, border_thickness);
  drawCircle(painter, QPointF(width() / 2, height() / 2), 120, middle_color, Qt::NoPen, 0);
  drawCircle(painter, QPointF(width() / 2, height() / 2), 55, inner_color, Qt::NoPen, 0);

  // Draws the alert ball
  QColor alert_color = getAlertColor(confidence);
  painter.setBrush(alert_color);
  QPointF ball_pos = calculateAlertBallPosition();
  painter.drawEllipse(ball_pos, 20, 20); // edit the ball size
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

// MODIFY - This should calculate the position of the alert ball
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
