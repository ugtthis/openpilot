#include "selfdrive/ui/qt/widgets/driver_alert_dial.h"
#include <algorithm>

// Implements createRadialGradient function
QRadialGradient DriverAlertDial::createRadialGradient(const QPointF &center,
                                                      int radius,
                                                      int blur_radius,
                                                      const QColor &color,
                                                      int opacity,
                                                      int x_offset,
                                                      int y_offset) {
  QRadialGradient gradient(center + QPointF(x_offset, y_offset), radius + blur_radius);

  QColor intenseColor = color;
  intenseColor.setAlpha(opacity);

  QColor midColor = color;
  midColor.setAlpha(opacity * 0.8);

  QColor transparentColor = color;
  transparentColor.setAlpha(0);

  gradient.setColorAt(0, intenseColor);
  gradient.setColorAt(0.7, midColor);
  gradient.setColorAt(1, transparentColor);

  return gradient;
}

// Constructor: This initializes the widgets properties
DriverAlertDial::DriverAlertDial(QWidget *parent) : QWidget(parent),
    confidence(cereal::ModelDataV2::ConfidenceClass::GREEN),
    steering_torque(0.0),
    brake_pressure(0.0),
    acceleration(0.0) {

  setFixedSize(390, 390); //Set the widget size
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
                                  int shadow_opacity,
                                  int shadow_x,
                                  int shadow_y) {
  // Draws shadow IF applicable
  if (shadow_blur_radius > 0 && shadow_opacity > 0) {
    QRadialGradient gradient = createRadialGradient(center,
                                                    radius,
                                                    shadow_blur_radius,
                                                    shadow_color,
                                                    shadow_opacity,
                                                    shadow_x,
                                                    shadow_y);

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
    painter.fillPath(shadow_path, QBrush(gradient));
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

      // Fill color
      properties.outerColor = QColor(21, 21, 21);
      properties.middleColor = QColor(21, 21, 21);
      properties.innerColor = QColor(16, 68, 79);

      // Border color
      properties.outerBorderColor = QColor(79, 16, 16);
      properties.middleBorderColor = QColor(79, 77, 16);
      properties.innerBorderColor = QColor(0, 209, 255);

      // Shadow color
      properties.outerShadowColor = QColor(0, 209, 255);
      properties.middleShadowColor = QColor(0, 0, 0);
      properties.innerShadowColor = QColor(0, 209, 255);

      // Shadow blur radius
      properties.outerShadowBlurRadius = 40;
      properties.middleShadowBlurRadius = 0;
      properties.innerShadowBlurRadius = 80;

      // Shadow opacity
      properties.outerShadowOpacity = 245;
      properties.middleShadowOpacity = 0;
      properties.innerShadowOpacity = 150;

      // Border thickness
      properties.outerBorderThickness = 20;
      properties.middleBorderThickness = 10;
      properties.innerBorderThickness = 10;

      properties.shadowX = 0;
      properties.shadowY = 0;

      // Alert ball fill color
      properties.alertBallOuterColor = QColor(233, 233, 233);
      properties.alertBallInnerColor = QColor(0, 209, 255);

      // Alert ball border color
      properties.alertBallOuterBorderColor = QColor(0, 0, 0);
      properties.alertBallInnerBorderColor = QColor(0, 0, 0);

      // Alert ball border thickness
      properties.alertBallOuterBorderThickness = 7;
      properties.alertBallInnerBorderThickness = 7;

      break;

    case cereal::ModelDataV2::ConfidenceClass::YELLOW: // Medium Alert

      // Fill color
      properties.outerColor = QColor(28, 27, 21);
      properties.middleColor = QColor(80, 77, 16);
      properties.innerColor = QColor(80, 77, 16);

      // Border color
      properties.outerBorderColor = QColor(79, 16, 16);
      properties.middleBorderColor = QColor(255, 245, 16);
      properties.innerBorderColor = QColor(60 , 102, 76);

      // Shadow color
      properties.outerShadowColor = QColor(255, 245, 0);
      properties.middleShadowColor = QColor(255, 245, 0);
      properties.innerShadowColor = QColor(0, 0, 0);

      // Shadow blur radius
      properties.outerShadowBlurRadius = 40;
      properties.middleShadowBlurRadius = 90;
      properties.innerShadowBlurRadius = 0;

      // Shadow opacity
      properties.outerShadowOpacity = 245;
      properties.middleShadowOpacity = 160;
      properties.innerShadowOpacity = 0;

      // Border thickness
      properties.outerBorderThickness = 20;
      properties.middleBorderThickness = 10;
      properties.innerBorderThickness = 10;

      properties.shadowX = 0;
      properties.shadowY = 0;

      // Alert ball fill color
      properties.alertBallOuterColor = QColor(233, 233, 233);
      properties.alertBallInnerColor = QColor(255, 245, 0);

      // Alert ball border color
      properties.alertBallOuterBorderColor = QColor(0, 0, 0);
      properties.alertBallInnerBorderColor = QColor(0, 0, 0);

      // Alert ball border thickness
      properties.alertBallOuterBorderThickness = 7;
      properties.alertBallInnerBorderThickness = 7;

      break;

    case cereal::ModelDataV2::ConfidenceClass::RED: // High Alert

      // Fill color
      properties.outerColor = QColor(13, 13, 13);
      properties.middleColor = QColor(13, 13, 13);
      properties.innerColor = QColor(13, 13, 13);

      // Border color
      properties.outerBorderColor = QColor(255, 0, 0);
      properties.middleBorderColor = QColor(30, 30, 30);
      properties.innerBorderColor = QColor(30, 30, 30);

      // Shadow color
      properties.outerShadowColor = QColor(255, 0, 0);
      properties.middleShadowColor = QColor(0, 0, 0);
      properties.innerShadowColor = QColor(0, 0, 0);

      // Shadow blur radius
      properties.outerShadowBlurRadius = 40;
      properties.middleShadowBlurRadius = 0;
      properties.innerShadowBlurRadius = 0;

      // Shadow opacity
      properties.outerShadowOpacity = 245;
      properties.middleShadowOpacity = 0;
      properties.innerShadowOpacity = 0;

      // Border thickness
      properties.outerBorderThickness = 20;
      properties.middleBorderThickness = 10;
      properties.innerBorderThickness = 10;

      properties.shadowX = 0;
      properties.shadowY = 0;

      // Alert ball fill color
      properties.alertBallOuterColor = QColor(255, 0, 0);
      properties.alertBallInnerColor = QColor(255, 0, 0);

      // Alert ball border color
      properties.alertBallOuterBorderColor = QColor(127, 0, 0);
      properties.alertBallInnerBorderColor = QColor(0, 0, 0, 0);

      // Alert ball border thickness
      properties.alertBallOuterBorderThickness = 10;
      properties.alertBallInnerBorderThickness = 0;

      break;

    default: // Disabled state

      // Fill color
      properties.outerColor = QColor(108, 108, 108);
      properties.middleColor = QColor(108, 108, 108);
      properties.innerColor = QColor(108, 108, 108);

      // Border color
      properties.outerBorderColor = QColor(79, 16, 16);
      properties.middleBorderColor = QColor(79, 16, 16);
      properties.innerBorderColor = QColor(79, 16, 16);

      // Shadow color
      properties.outerShadowColor = QColor(0, 0, 0);
      properties.middleShadowColor = QColor(0, 0, 0);
      properties.innerShadowColor = QColor(0, 0, 0);

      // Shadow blur radius
      properties.outerShadowBlurRadius = 0;
      properties.middleShadowBlurRadius = 0;
      properties.innerShadowBlurRadius = 0;

      // Shadow opacity
      properties.outerShadowOpacity = 0;
      properties.middleShadowOpacity = 0;
      properties.innerShadowOpacity = 0;

      // Border thickness
      properties.outerBorderThickness = 20;
      properties.middleBorderThickness = 10;
      properties.innerBorderThickness = 10;

      properties.shadowX = 0;
      properties.shadowY = 0;

      // Alert ball fill color
      // Made it transparent since ball is not needed when disabled
      properties.alertBallOuterColor = QColor(0, 0, 0, 0);
      properties.alertBallInnerColor = QColor(0, 0, 0, 0);

      // Alert ball border color
      properties.alertBallOuterBorderColor = QColor(0, 0, 0);
      properties.alertBallInnerBorderColor = QColor(0, 0, 0);

      // Alert ball border thickness
      properties.alertBallOuterBorderThickness = 0;
      properties.alertBallInnerBorderThickness = 0;

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
            155,
            properties.outerColor,
            properties.outerBorderColor,
            properties.outerBorderThickness,
            properties.outerShadowColor,
            properties.outerShadowBlurRadius,
            properties.outerShadowOpacity,
            properties.shadowX,
            properties.shadowY);

  // Draws the middle circle
  drawCircle(painter,
            QPointF(width() / 2, height() / 2),
            100,
            properties.middleColor,
            properties.middleBorderColor,
            properties.middleBorderThickness,
            properties.middleShadowColor,
            properties.middleShadowBlurRadius / 4,
            properties.middleShadowOpacity,
            properties.shadowX,
            properties.shadowY);

  // Draws the inner circle
  drawCircle(painter,
            QPointF(width() / 2, height() / 2),
            35,
            properties.innerColor,
            properties.innerBorderColor,
            properties.innerBorderThickness,
            properties.innerShadowColor,
            properties.innerShadowBlurRadius / 4,
            properties.innerShadowOpacity,
            properties.shadowX,
            properties.shadowY);

  // Draws the alert ball
  QPointF ball_pos = calculateAlertBallPosition();
  int outer_ball_radius = 50 / 2;
  int inner_ball_radius = 24 / 2;

  // Draws outer part of ball
  painter.setBrush(properties.alertBallOuterColor);
  painter.setPen(QPen(properties.alertBallOuterBorderColor, properties.alertBallOuterBorderThickness));
  painter.drawEllipse(ball_pos.x() - outer_ball_radius, ball_pos.y() - outer_ball_radius, 50, 50);

  // Draws the inner part of ball
  painter.setBrush(properties.alertBallInnerColor);
  painter.setPen(QPen(properties.alertBallInnerBorderColor, properties.alertBallInnerBorderThickness));
  painter.drawEllipse(ball_pos.x() - inner_ball_radius, ball_pos.y() - inner_ball_radius, 24, 24);

  // Box around UI to see sizing easier - REMOVE soon
  QPen borderPen(Qt::blue, 2);
  painter.setPen(borderPen);
  painter.setBrush(Qt::NoBrush);

  // Draw a rectangle around the widget
  QRectF borderRect(0, 0, width(), height());
  painter.drawRect(borderRect);

  // Restore painter state
  painter.restore();
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
